#pragma once
#include "common.hpp"
#include <sqlite3.h>
namespace pa {
class Store {
  sqlite3 *db_ = nullptr;

public:
  std::recursive_mutex mutex;
  explicit Store(const fs::path &path) {
    fs::create_directories(path.parent_path());
    if (sqlite3_open(path_utf8(path).c_str(), &db_) != SQLITE_OK)
      throw std::runtime_error("SQLite open failed");
    sqlite3_busy_timeout(db_, 5000);
    require(rows("PRAGMA user_version")[0]["user_version"].get<int>() <= 1, 409,
            "Database is newer than this service; refusing downgrade");
    exec("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA "
         "foreign_keys=ON;");
    exec("CREATE TABLE IF NOT EXISTS resources(kind TEXT NOT NULL,id TEXT NOT "
         "NULL,rev INTEGER NOT NULL,data TEXT NOT NULL,updated INTEGER NOT "
         "NULL,PRIMARY KEY(kind,id));"
         "CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY "
         "AUTOINCREMENT,kind TEXT NOT NULL,id TEXT NOT NULL,rev INTEGER NOT "
         "NULL,at INTEGER NOT NULL);"
         "CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY,source_id "
         "TEXT NOT NULL,hash TEXT NOT NULL,body TEXT NOT NULL,meta TEXT NOT "
         "NULL,at INTEGER NOT NULL,UNIQUE(source_id,hash));"
         "CREATE INDEX IF NOT EXISTS versions_source ON versions(source_id,at);"
         "CREATE TABLE IF NOT EXISTS requests(key TEXT PRIMARY KEY,hash TEXT "
         "NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL);"
         "CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY,week INTEGER "
         "NOT NULL,reserved REAL NOT NULL,actual REAL,status TEXT NOT NULL,at "
         "INTEGER NOT NULL);"
         "PRAGMA user_version=1;");
  }
  ~Store() { sqlite3_close(db_); }
  Store(const Store &) = delete;
  void exec(const std::string &s) {
    char *err = nullptr;
    if (sqlite3_exec(db_, s.c_str(), nullptr, nullptr, &err) != SQLITE_OK) {
      std::string m = err ? err : "SQLite error";
      sqlite3_free(err);
      throw std::runtime_error(m);
    }
  }
  json rows(const std::string &s, const json &params = json::array()) {
    sqlite3_stmt *raw = nullptr;
    if (sqlite3_prepare_v2(db_, s.c_str(), -1, &raw, nullptr) != SQLITE_OK)
      throw std::runtime_error(sqlite3_errmsg(db_));
    std::unique_ptr<sqlite3_stmt, decltype(&sqlite3_finalize)> st(
        raw, sqlite3_finalize);
    for (size_t i = 0; i < params.size(); ++i) {
      auto &v = params[i];
      int rc = SQLITE_OK;
      if (v.is_null())
        rc = sqlite3_bind_null(raw, (int)i + 1);
      else if (v.is_number_integer())
        rc = sqlite3_bind_int64(raw, (int)i + 1, v.get<int64_t>());
      else if (v.is_number())
        rc = sqlite3_bind_double(raw, (int)i + 1, v.get<double>());
      else {
        auto t = v.get<std::string>();
        rc = sqlite3_bind_text(raw, (int)i + 1, t.data(), (int)t.size(),
                               SQLITE_TRANSIENT);
      }
      if (rc != SQLITE_OK)
        throw std::runtime_error("SQLite bind error");
    }
    json out = json::array();
    int rc;
    while ((rc = sqlite3_step(raw)) == SQLITE_ROW) {
      json r = json::object();
      for (int i = 0; i < sqlite3_column_count(raw); ++i) {
        auto k = sqlite3_column_name(raw, i);
        switch (sqlite3_column_type(raw, i)) {
        case SQLITE_NULL:
          r[k] = nullptr;
          break;
        case SQLITE_INTEGER:
          r[k] = sqlite3_column_int64(raw, i);
          break;
        case SQLITE_FLOAT:
          r[k] = sqlite3_column_double(raw, i);
          break;
        default:
          r[k] = (const char *)sqlite3_column_text(raw, i);
        }
      }
      out.push_back(r);
    }
    if (rc != SQLITE_DONE)
      throw std::runtime_error(sqlite3_errmsg(db_));
    return out;
  }
  struct Tx {
    Store &s;
    bool done = false;
    std::string name;
    Tx(Store &v) : s(v), name("tx_" + uid()) { s.exec("SAVEPOINT " + name); }
    void commit() {
      s.exec("RELEASE SAVEPOINT " + name);
      done = true;
    }
    ~Tx() {
      if (!done) {
        try {
          s.exec("ROLLBACK TO SAVEPOINT " + name);
          s.exec("RELEASE SAVEPOINT " + name);
        } catch (...) {
        }
      }
    }
  };
  json get(const std::string &kind, const std::string &id) {
    auto r =
        rows("SELECT data FROM resources WHERE kind=? AND id=?", {kind, id});
    if (r.empty())
      throw Error(404, "Resource not found");
    return json::parse(r[0]["data"].get<std::string>());
  }
  json list(const std::string &kind) {
    auto rs =
        rows("SELECT data FROM resources WHERE kind=? ORDER BY updated DESC,id",
             {kind});
    json out = json::array();
    for (auto &r : rs)
      out.push_back(json::parse(r["data"].get<std::string>()));
    return out;
  }
  json put(const std::string &kind, json data, int expected = -1) {
    std::unique_ptr<Tx> atomic;
    if (sqlite3_get_autocommit(db_))
      atomic = std::make_unique<Tx>(*this);
    auto id = data.value("id", uid());
    auto existing =
        rows("SELECT rev FROM resources WHERE kind=? AND id=?", {kind, id});
    int rev = existing.empty() ? 0 : existing[0]["rev"].get<int>();
    if (expected >= 0 && expected != rev)
      throw Error(409, "Revision conflict",
                  {{"current", existing.empty() ? json() : get(kind, id)}});
    data["id"] = id;
    data["revision"] = rev + 1;
    data["updated_at"] = now();
    rows("INSERT INTO resources VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO "
         "UPDATE SET "
         "rev=excluded.rev,data=excluded.data,updated=excluded.updated",
         {kind, id, rev + 1, data.dump(), now()});
    rows("INSERT INTO events(kind,id,rev,at) VALUES(?,?,?,?)",
         {kind, id, rev + 1, now()});
    if (atomic)
      atomic->commit();
    return data;
  }
  int64_t cursor() {
    return rows("SELECT COALESCE(MAX(seq),0) AS seq FROM events")[0]["seq"]
        .get<int64_t>();
  }
  void backup(const fs::path &p) {
    sqlite3 *dest = nullptr;
    if (sqlite3_open(path_utf8(p).c_str(), &dest) != SQLITE_OK) {
      if (dest)
        sqlite3_close(dest);
      throw std::runtime_error("Backup open failed");
    }
    auto b = sqlite3_backup_init(dest, "main", db_, "main");
    if (!b) {
      sqlite3_close(dest);
      throw std::runtime_error("Backup init failed");
    }
    int rc = sqlite3_backup_step(b, -1);
    int fin = sqlite3_backup_finish(b);
    sqlite3_close(dest);
    if (rc != SQLITE_DONE || fin != SQLITE_OK)
      throw std::runtime_error("Backup incomplete");
  }
};
} // namespace pa
