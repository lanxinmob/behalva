#pragma once
#include "providers.hpp"
namespace pa {
class Service {
public:
  json attachment(const json &j) {
    auto name = field(j, "name", 255), encoded = field(j, "base64", 6 * 1024 * 1024);
    require(name.find_first_of("/\\\r\n") == std::string::npos, 400,
            "Attachment name must be a filename");
    auto bytes = unbase64(encoded);
    auto digest = hash(bytes);
    auto id = hash(name + ":" + digest);
    auto existing = db.rows("SELECT data FROM resources WHERE kind='attachments' AND id=?", {id});
    if (!existing.empty())
      return json::parse(existing[0]["data"].get<std::string>());
    return db.put("attachments", {{"id", id},
                                  {"name", name},
                                  {"sha256", digest},
                                  {"size", bytes.size()},
                                  {"base64", encoded}});
  }
  json attachments_for_approval(const json &items) {
    require(items.is_array() && items.size() <= 20, 400, "At most 20 attachments");
    json result = json::array();
    for (auto &id : items) {
      require(id.is_string(), 400, "Attachment IDs required");
      auto a = db.get("attachments", id.get<std::string>());
      require(hash(unbase64(a["base64"])) == a["sha256"].get<std::string>(), 409,
              "Attachment integrity failure");
      a.erase("base64");
      a.erase("revision");
      a.erase("updated_at");
      result.push_back(a);
    }
    return result;
  }
  json attachment_extraction(const std::string &id, const json &j) {
    auto a = db.get("attachments", id);
    auto state = field(j, "state", 32);
    require(state == "indexed" || state == "needs_input", 400, "Invalid extraction state");
    a["extraction_status"] = state;
    a["extraction_message"] = field(j, "message", 2000);
    if (state == "indexed") {
      auto sid = field(j, "source_id", 64);
      auto source = db.get("sources", sid);
      require(source["kind"] == "shared" && source["uri"] == "attachment:" + id, 409,
              "Extracted source does not belong to attachment");
      a["source_id"] = sid;
    }
    return db.put("attachments", a);
  }
  Store db;
  fs::path data;
  json config;
  ModelProviders providers;
  Service(fs::path root, json cfg) : db(root / "assistant.db"), data(root), config(cfg), providers(db, root) {
    std::lock_guard lock(db.mutex);
    Store::Tx tx(db);
    for (auto t : db.list("tasks"))
      if (t.value("state", "") == "running") {
        t["state"] = "blocked";
        t["notice"] = "Service restarted; inspect saved results before resuming";
        db.put("tasks", t);
      }
    for (auto o : db.list("operations"))
      if (o.value("state", "") == "executing") {
        o["state"] = "unknown";
        o["notice"] = "Service restarted; verify external result before any retry";
        db.put("operations", o);
      }
    // A lost model response still may have been billed. Retain its reservation.
    db.rows("UPDATE usage SET status='unknown' WHERE status='pending'");
    for (auto run : db.list("model_runs"))
      if (run["state"] == "pending") {
        run["state"] = "unknown";
        run["notice"] = "Service restarted before model result was saved";
        db.put("model_runs", run);
      }
    tx.commit();
  }
  json provider_config() {
    auto id = providers.selected();
    auto p = id.empty() ? config.value("provider", json::object()) : providers.get(id);
    if (!p.empty() && !p.contains("ca_bundle") && config.contains("ca_bundle"))
      p["ca_bundle"] = config["ca_bundle"];
    return p;
  }
  std::string provider_key(const json &p) {
    if (p.contains("id")) return providers.key(p["id"]);
    auto env = p.value("api_key_env", "PA_API_KEY"); auto value = std::getenv(env.c_str());
    require(value && *value, 503, "Model API key environment variable is not set");
    return value;
  }
  bool model_configured() {
    auto p = provider_config();
    if (p.value("credential_unavailable", false)) return false;
    if (!p.contains("model") || !p["model"].is_string() || p["model"].get<std::string>().empty()) return false;
    try { ModelProviders::pricing(p); return true; }
    catch (const Error &) { return false; }
  }
  // Matching device without its credential hash, active or revoked; null if none.
  json device_for(const std::string &authorization) {
    auto digest = hash(authorization);
    for (auto d : db.list("devices"))
      if (equal_secret(digest, d.value("credential_hash", ""))) {
        d.erase("credential_hash");
        return d;
      }
    return nullptr;
  }
  bool device_authorized(const std::string &authorization) {
    auto d = device_for(authorization);
    return d.is_object() && d.value("active", false);
  }
  json devices() {
    auto ds = db.list("devices");
    for (auto &d : ds)
      d.erase("credential_hash");
    return ds;
  }
  json pair_device(const json &j) {
    auto token = uid() + uid();
    auto d = db.put("devices",
                    {{"name", field(j, "name", 200)},
                     {"active", true},
                     {"credential_hash", hash("Bearer " + token)}},
                    0);
    d.erase("credential_hash");
    d["token"] = token;
    return d;
  }
  json revoke_device(const std::string &id) {
    auto d = db.get("devices", id);
    d["active"] = false;
    d = db.put("devices", d);
    d.erase("credential_hash");
    return d;
  }
  json state() {
    recover_expired_tasks();
    json out;
    for (auto k :
         {"tasks", "drafts", "goals", "rules", "operations", "grants", "groups", "sources",
          "tables", "conflicts", "updates", "schedules", "model_runs", "conversations", "messages",
          "connection_checks"})
      out[k] = db.list(k);
    out["devices"] = devices();
    out["attachments"] = db.list("attachments");
    for (auto &attachment : out["attachments"])
      attachment.erase("base64");
    out["cursor"] = db.cursor();
    out["budget"] = budget();
    out["model_configured"] = model_configured();
    return out;
  }
  json approve_model_retry(const std::string &id, const json &j) {
    auto run = db.get("model_runs", id);
    auto task = db.get("tasks", run["task_id"]);
    require(run["revision"] == j.value("expected_revision", 0), 409, "Model run changed");
    require(run["state"] == "unknown" && !run.value("retry_authorized", false), 409,
            "Only unresolved model calls may be retried by explicit approval");
    require(task["state"] != "running" && task["state"] != "completed", 409,
            "Wait for the current task attempt to stop");
    require(j.value("accept_unknown_cost", false), 400, "Acknowledge the retained unknown cost");
    run["retry_reason"] = field(j, "reason", 2000);
    run["retry_authorized"] = true;
    Store::Tx tx(db);
    run = db.put("model_runs", run);
    task["state"] = "queued";
    db.put("tasks", task);
    tx.commit();
    return run;
  }
  json source_availability(const std::string &id, const json &j) {
    auto source = db.get("sources", id);
    require(source["kind"] == "file", 400, "Only local file availability may be updated");
    source["available"] = j.value("available", false);
    source["availability_reason"] = field(j, "reason", 2000);
    source["checked_at"] = now();
    return db.put("sources", source);
  }
  json resolve_conflict(const std::string &id, const json &j) {
    auto c = db.get("conflicts", id);
    require(c["state"] == "unresolved", 409, "Conflict already resolved");
    auto current = db.get("drafts", c["draft_id"]);
    require(current["revision"] == j.value("expected_revision", 0), 409,
            "Draft changed again; refresh before choosing");
    auto choice = field(j, "choice", 32);
    require(choice == "local" || choice == "remote", 400, "Choose local or remote");
    Store::Tx tx(db);
    if (choice == "local") {
      auto v = c["local"];
      if (j.contains("local")) {
        v["title"] = field(j["local"], "title", 2000);
        require(j["local"].contains("body") && j["local"]["body"].is_string(), 400,
                "Draft body required");
        v["body"] = j["local"]["body"];
        c["selected_local"] = v;
      }
      v["id"] = current["id"];
      current = db.put("drafts", v, j["expected_revision"]);
    }
    c["state"] = "resolved";
    c["choice"] = choice;
    db.put("conflicts", c);
    tx.commit();
    return current;
  }
  void sync_rule_files() {
    for (auto r : db.list("rules")) {
      if (r["state"] != "approved")
        continue;
      auto path = data / "rules" / (r["id"].get<std::string>() + ".md");
      if (!fs::exists(path))
        continue;
      auto content = read(path);
      auto h = hash(content);
      if (r.value("file_hash", "") == h)
        continue;
      require(content.size() <= 40000, 413, "Edited rule exceeds size limit");
      Store::Tx tx(db);
      db.put("rule_history", {{"rule_id", r["id"]}, {"previous", r}}, 0);
      r["body"] = content;
      r["hash"] = h;
      r["file_hash"] = h;
      db.put("rules", r);
      tx.commit();
    }
  }
  json create_task(const json &j) {
    auto title = field(j, "title", 20000);
    std::string key = j.value("request_id", uid());
    require(key.size() <= 128, 400, "Request id too long");
    auto digest = hash(j.dump());
    Store::Tx tx(db);
    auto old = db.rows("SELECT hash,id FROM requests WHERE key=?", {key});
    if (!old.empty()) {
      require(old[0]["hash"] == digest, 409, "Request id reused with different content");
      auto result = db.get("tasks", old[0]["id"]);
      tx.commit();
      return result;
    }
    json t = {{"title", title},
              {"state", "queued"},
              {"priority", j.value("priority", 1)},
              {"urls", j.value("urls", json::array())},
              {"source_ids", j.value("source_ids", json::array())},
              {"created_at", now()}};
    require(t["priority"].is_number_integer() && t["urls"].is_array() && t["source_ids"].is_array(),
            400, "Invalid task parameters");
    t = db.put("tasks", t, 0);
    db.rows("INSERT INTO requests VALUES(?,?,?,?)", {key, digest, "tasks", t["id"]});
    tx.commit();
    return t;
  }
  json claim_task(const std::string &id) {
    auto t = db.get("tasks", id);
    require(t["state"] == "queued", 409, "Task is not queued");
    t["state"] = "running";
    t["attempt_id"] = uid();
    t["lease_until"] = now() + 300;
    return db.put("tasks", t);
  }
  void recover_expired_tasks() {
    for (auto t : db.list("tasks"))
      if (t.value("state", "") == "running" && t.value("lease_until", int64_t(0)) < now()) {
        t["state"] = "blocked";
        t["result"] = {{"message", "Worker lease expired. Inspect saved results before "
                                   "resuming; no automatic model or external retry."}};
        db.put("tasks", t);
      }
  }
  json heartbeat(const std::string &id, const json &j) {
    auto t = db.get("tasks", id);
    require(t["state"] == "running" && t.value("attempt_id", "") == field(j, "attempt_id", 64), 409,
            "Stale task attempt");
    t["lease_until"] = now() + 300;
    return db.put("tasks", t);
  }
  json task_result(const std::string &id, const json &j) {
    auto t = db.get("tasks", id);
    if (t.value("kind", "") == "chat" && t["state"] == "completed" &&
        t.value("attempt_id", "") == j.value("attempt_id", ""))
      return t; // Model result and assistant message were committed atomically.
    require(t["state"] == "running", 409, "Task is not running");
    require(t.value("attempt_id", "") == field(j, "attempt_id", 64), 409, "Stale task attempt");
    auto s = field(j, "state", 64);
    require(std::set<std::string>{"completed", "waiting_input", "waiting_authorization", "blocked",
                                  "failed"}
                .contains(s),
            400, "Invalid task result state");
    t["state"] = s;
    t["result"] = j.value("result", json::object());
    return db.put("tasks", t);
  }
  json resume_task(const std::string &id, const json &j) {
    auto t = db.get("tasks", id);
    require(t.value("kind", "") != "feature", 409, "功能需求等待开发安排，不能作为调查任务运行。");
    if (t.value("kind", "") == "chat")
      require(j.value("reply", "").empty(), 400, "请在聊天页发送新消息；恢复只继续原消息。");
    require(t["state"] != "running" && t["state"] != "completed", 409, "Cannot resume this task");
    t["state"] = "queued";
    t["reply"] = j.value("reply", "");
    return db.put("tasks", t);
  }
  json draft(const json &j, const std::string &id = "") {
    field(j, "title", 2000);
    require(j.contains("body") && j["body"].is_string(), 400, "Draft body required");
    json v = {{"title", j["title"]}, {"body", j["body"]}};
    if (j.contains("task_id"))
      v["task_id"] = j["task_id"];
    if (id.empty())
      return db.put("drafts", v, 0);
    auto old = db.get("drafts", id);
    int expected = j.value("expected_revision", -1);
    require(expected >= 1, 400, "Expected revision required");
    if (old["revision"] != expected) {
      json c = {{"draft_id", id},
                {"request_id", j.value("request_id", "")},
                {"base_revision", expected},
                {"local", v},
                {"remote", old},
                {"state", "unresolved"}};
      c = db.put("conflicts", c, 0);
      throw Error(409, "Draft changed; choose a version", {{"conflict", c}});
    }
    v["id"] = id;
    return db.put("drafts", v, expected);
  }
  json group(const json &j) {
    auto id = field(j, "id", 32);
    require(std::all_of(id.begin(), id.end(), ::isdigit), 400, "Group id must contain digits");
    auto active = j.value("active", true);
    json v = {{"id", id},
              {"active", active},
              {"title", j.value("title", id)},
              {"history_days", j.value("history_days", 0)}};
    return db.put("groups", v);
  }
  bool group_active(const std::string &id) {
    try {
      return db.get("groups", id).value("active", false);
    } catch (const Error &) {
      return false;
    }
  }
  json ingest(const json &j) {
    auto uri = field(j, "uri", 8192), body = field(j, "body", 4 * 1024 * 1024),
         kind = field(j, "kind", 32);
    require(std::set<std::string>{"file", "web", "qq", "shared"}.contains(kind), 400,
            "Invalid source kind");
    std::string gid = j.value("group_id", "");
    if (kind == "qq")
      require(group_active(gid), 403, "QQ group not authorized");
    if (kind == "web")
      require(uri.starts_with("https://") || uri.starts_with("http://"), 400,
              "Web source requires HTTP URL");
    if (kind == "file") {
      fs::path p = path_from_utf8(uri);
      require(p.is_absolute(), 400, "File URI must be absolute");
      uri = path_utf8(fs::weakly_canonical(p));
    }
    if (kind == "qq")
      uri = "qq:" + gid + ":" + uri;
    auto sid = hash(kind + ":" + uri);
    auto digest = hash(body);
    Store::Tx tx(db);
    // A platform migration changes the file path but keeps historical source IDs.
    if (kind == "file") {
      auto prior = db.rows("SELECT id FROM resources WHERE kind='sources' AND "
                           "json_extract(data,'$.kind')='file' AND json_extract(data,'$.uri')=?", {uri});
      require(prior.size() <= 1, 409, "Multiple sources share this file location");
      if (!prior.empty()) sid = prior[0]["id"].get<std::string>();
    }
    auto vs = db.rows("SELECT id FROM versions WHERE source_id=? AND hash=?", {sid, digest});
    json meta = {{"uri", uri},
                 {"kind", kind},
                 {"title", j.value("title", uri)},
                 {"group_id", gid},
                 {"origin_time", j.value("origin_time", json())},
                 {"coverage", j.value("coverage", "provided content only")}};
    std::string vid = vs.empty() ? uid() : vs[0]["id"].get<std::string>();
    if (vs.empty())
      db.rows("INSERT INTO versions VALUES(?,?,?,?,?,?)",
              {vid, sid, digest, body, meta.dump(), now()});
    json source = meta;
    source["id"] = sid;
    source["version_id"] = vid;
    source["hash"] = digest;
    source["cloud_default"] = (kind == "web" || kind == "qq");
    source["available"] = true;
    source["preview"] = prefix(body, 200);
    source = db.put("sources", source);
    tx.commit();
    return source;
  }
  json grant(const json &j) {
    auto scope = field(j, "scope", 32), target = field(j, "target", 8192);
    require(scope == "source" || scope == "directory", 400, "Invalid grant scope");
    if (scope == "source")
      db.get("sources", target);
    else {
      fs::path p = path_from_utf8(target);
      require(p.is_absolute(), 400, "Absolute directory required");
      target = path_utf8(fs::weakly_canonical(p));
    }
    Store::Tx tx(db);
    auto id = hash(scope + target);
    auto prior = db.rows("SELECT id FROM resources WHERE kind='grants' AND "
                         "json_extract(data,'$.scope')=? AND json_extract(data,'$.target')=?", {scope, target});
    require(prior.size() <= 1, 409, "Multiple grants share this target");
    if (!prior.empty()) id = prior[0]["id"].get<std::string>();
    auto result = db.put("grants", {{"id", id},
                             {"scope", scope},
                             {"target", target},
                             {"active", j.value("active", true)}});
    tx.commit();
    return result;
  }
  bool allowed(const json &s) {
    if (s.value("cloud_default", false))
      return true;
    for (auto &g : db.list("grants")) {
      if (!g.value("active", false))
        continue;
      if (g["scope"] == "source" && g["target"] == s["id"])
        return true;
      if (g["scope"] == "directory" && s["kind"] == "file" &&
          within(path_from_utf8(s["uri"].get<std::string>()),
                 path_from_utf8(g["target"].get<std::string>())))
        return true;
    }
    return false;
  }
  json version(const std::string &id) {
    auto v = db.rows("SELECT * FROM versions WHERE id=?", {id});
    require(!v.empty(), 404, "Version not found");
    v[0]["meta"] = json::parse(v[0]["meta"].get<std::string>());
    return v[0];
  }
  json search(const std::string &q) {
    require(!q.empty() && q.size() <= 1000, 400, "Search query required");
    json out = json::array();
    auto rs = db.rows("SELECT v.id,v.source_id,v.body,v.meta,v.at FROM versions v "
                      "JOIN resources r ON r.kind='sources' AND r.id=v.source_id AND "
                      "json_extract(r.data,'$.version_id')=v.id WHERE "
                      "coalesce(json_extract(r.data,'$.available'),1)=1 AND ("
                      "instr(lower(v.body),lower(?))>0 OR "
                      "instr(lower(v.meta),lower(?))>0) LIMIT 80",
                      {q, q});
    for (auto &r : rs) {
      auto body = r["body"].get<std::string>();
      auto at = ascii_lower(body).find(ascii_lower(q));
      r["match_in"] = at == std::string::npos ? "metadata" : "body";
      if (at == std::string::npos)
        at = 0;
      auto begin = body.rfind('\n', at);
      begin = begin == std::string::npos ? 0 : begin + 1;
      auto end = body.find('\n', at + q.size());
      if (end == std::string::npos)
        end = body.size();
      r["excerpt"] = body.substr(begin, end - begin);
      r["line"] = 1 + std::count(body.begin(), body.begin() + begin, '\n');
      r.erase("body");
      r["meta"] = json::parse(r["meta"].get<std::string>());
      r["cloud_allowed"] = allowed(db.get("sources", r["source_id"]));
      out.push_back(r);
    }
    return out;
  }
  json context(const json &ids) {
    require(ids.is_array() && ids.size() <= 30, 400, "At most 30 source IDs");
    json docs = json::array(), needs = json::array();
    size_t bytes = 0;
    for (auto &id : ids) {
      auto s = db.get("sources", id.get<std::string>());
      if (!s.value("available", true))
        throw Error(409, "Source is no longer available as current evidence",
                    {{"source_id", id}, {"reason", s.value("availability_reason", "")}});
      if (!allowed(s)) {
        needs.push_back({{"id", id}, {"title", s["title"]}});
        continue;
      }
      auto v = version(s["version_id"]);
      bytes += v["body"].get<std::string>().size();
      require(bytes <= 250000, 413, "Context too large; select fewer sources");
      docs.push_back(
          {{"source_id", id}, {"version_id", v["id"]}, {"uri", s["uri"]}, {"body", v["body"]}});
    }
    if (!needs.empty())
      throw Error(403, "Source permission required", {{"needs_authorization", needs}});
    return docs;
  }
  json propose_rule(const json &j) {
    return db.put("rules",
                  {{"title", field(j, "title", 2000)},
                   {"body", field(j, "body", 30000)},
                   {"state", "proposed"},
                   {"hash", hash(j.at("body").get<std::string>())}},
                  0);
  }
  json approve_rule(const std::string &id, const json &j) {
    auto r = db.get("rules", id);
    require(r["revision"] == j.value("revision", 0) && r["hash"] == j.value("hash", ""), 409,
            "Rule changed");
    r["state"] = "approved";
    auto content =
        "# " + r["title"].get<std::string>() + "\n\n" + r["body"].get<std::string>() + "\n";
    write(data / "rules" / (id + ".md"), content);
    r["file_hash"] = hash(content);
    return db.put("rules", r);
  }
  void export_rules() {
    for (auto &r : db.list("rules"))
      if (r["state"] == "approved")
        write(data / "rules" / (r["id"].get<std::string>() + ".md"),
              "# " + r["title"].get<std::string>() + "\n\n" + r["body"].get<std::string>() + "\n");
  }
  json operation(const json &j, const std::string &id = "") {
    auto kind = field(j, "kind", 32);
    require(kind == "gmail" || kind == "form", 400, "Unsupported operation kind");
    require(j.contains("payload") && j["payload"].is_object(), 400, "Operation payload required");
    auto p = j["payload"];
    if (kind == "gmail") {
      field(p, "from", 1000);
      field(p, "to", 1000);
      field(p, "subject", 2000);
      field(p, "body", 100000);
      for (auto k : {"from", "to", "subject"})
        require(p[k].get<std::string>().find_first_of("\r\n") == std::string::npos, 400,
                "Invalid email header");
      p["attachments"] = attachments_for_approval(p.value("attachment_ids", json::array()));
      p.erase("attachment_ids");
    } else {
      field(p, "url", 8192);
      require(p.contains("fields") && p["fields"].is_object(), 400, "Form fields required");
      field(p, "submit_selector", 500);
    }
    json o = {{"kind", kind},
              {"payload", p},
              {"payload_hash", hash(p.dump())},
              {"state", "awaiting_approval"}};
    if (!id.empty()) {
      auto old = db.get("operations", id);
      require(old["state"] == "awaiting_approval" || old["state"] == "approved", 409,
              "Operation already started");
      require(old["revision"] == j.value("expected_revision", 0), 409, "Operation changed");
      o["id"] = id;
    }
    return db.put("operations", o, id.empty() ? 0 : j.value("expected_revision", 0));
  }
  json approve_operation(const std::string &id, const json &j) {
    auto o = db.get("operations", id);
    require(o["state"] == "awaiting_approval", 409, "Not awaiting approval");
    require(o["revision"] == j.value("revision", 0) &&
                o["payload_hash"] == j.value("payload_hash", ""),
            409, "Approval content changed");
    o["state"] = "approved";
    o["approved_hash"] = o["payload_hash"];
    return db.put("operations", o);
  }
  json claim_operation(const std::string &id) {
    auto o = db.get("operations", id);
    require(o["state"] == "approved" && o["approved_hash"] == hash(o["payload"].dump()), 409,
            "No valid approval");
    for (auto &a : o["payload"].value("attachments", json::array())) {
      auto blob = db.get("attachments", a["id"]);
      require(hash(unbase64(blob["base64"])) == a["sha256"].get<std::string>(), 409,
              "Approved attachment changed");
    }
    o["state"] = "executing";
    o["attempt_id"] = uid();
    return db.put("operations", o);
  }
  json operation_result(const std::string &id, const json &j) {
    auto o = db.get("operations", id);
    require(o["state"] == "executing" || o["state"] == "unknown", 409,
            "Operation is not in flight");
    require(o.value("attempt_id", "") == field(j, "attempt_id", 64), 409, "Attempt mismatch");
    auto s = field(j, "state", 32);
    require(s == "succeeded" || s == "failed" || s == "unknown", 400, "Invalid result");
    if (s == "succeeded")
      field(j, "evidence", 30000);
    o["state"] = s;
    o["evidence"] = j.value("evidence", "");
    return db.put("operations", o);
  }
  json budget() {
    auto week = (now() + 3 * 86400) / (7 * 86400);
    auto r = db.rows("SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) AS "
                     "spent,COALESCE(SUM(CASE WHEN status='estimated' THEN actual ELSE 0 END),0) AS estimated "
                     "FROM usage WHERE week=?",
                     {week});
    return {{"week", week},
            {"committed_usd", r[0]["spent"]},
            {"estimated_usd", r[0]["estimated"]},
            {"limit_usd", config.value("weekly_budget_usd", 50.0)}};
  }
  std::string reserve(double amount) {
    require(amount > 0 && amount <= 50, 400, "Invalid model cost reservation");
    auto b = budget();
    require(b["committed_usd"].get<double>() + amount <= b["limit_usd"].get<double>(), 402,
            "Weekly model budget exhausted");
    auto id = uid();
    db.rows("INSERT INTO usage VALUES(?,?,?,NULL,'pending',?)", {id, b["week"], amount, now()});
    return id;
  }
  void settle(const std::string &id, const json &actual, bool estimated = false) {
    require(actual.is_null() || (actual.is_number() && std::isfinite(actual.get<double>()) &&
                                 actual.get<double>() >= 0),
            400, "Invalid billed cost");
    db.rows("UPDATE usage SET actual=?,status=? WHERE id=?",
            {actual, actual.is_null() ? "unknown" : (estimated ? "estimated" : "settled"), id});
  }
};
} // namespace pa
