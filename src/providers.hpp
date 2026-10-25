#pragma once
#include "store.hpp"
#include "httplib.h"
#include <regex>
#include <cstring>
#ifdef _WIN32
#include <wincrypt.h>
#else
#include <sys/stat.h>
#endif
namespace pa {
inline std::string decode_hex(const std::string &text) {
  require(text.size() % 2 == 0, 500, "Invalid stored credential");
  std::string bytes;
  auto digit = [](char c) { return c >= '0' && c <= '9' ? c - '0' :
                                  c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; };
  for (size_t i = 0; i < text.size(); i += 2) {
    int a = digit(text[i]), b = digit(text[i + 1]);
    require(a >= 0 && b >= 0, 500, "Invalid stored credential");
    bytes.push_back(char(a * 16 + b));
  }
  return bytes;
}

// Credentials never enter resource events, sync, configuration files or API responses.
class ModelProviders {
  Store &db;
  fs::path root;
  std::string crypt(const std::string &value, bool encrypt) {
#ifdef _WIN32
    DATA_BLOB in{DWORD(value.size()), (BYTE *)value.data()}, out{};
    bool ok = encrypt ? CryptProtectData(&in, L"Personal assistant model key", nullptr,
                         nullptr, nullptr, CRYPTPROTECT_UI_FORBIDDEN, &out)
                      : CryptUnprotectData(&in, nullptr, nullptr, nullptr, nullptr,
                         CRYPTPROTECT_UI_FORBIDDEN, &out);
    require(ok, 503, "Credential store unavailable; re-enter key on this Windows account");
    std::string result((char *)out.pbData, out.cbData);
    SecureZeroMemory(out.pbData, out.cbData);
    LocalFree(out.pbData);
    return result;
#else
    auto path = root / "provider-master.key";
    int fd = ::open(path.c_str(), O_RDWR | O_CREAT | O_NOFOLLOW, 0600);
    require(fd >= 0, 503, "Credential store unavailable");
    struct stat info{};
    bool valid = fstat(fd, &info) == 0 && S_ISREG(info.st_mode) &&
                 info.st_uid == geteuid() && (info.st_mode & 077) == 0;
    unsigned char master[32];
    if (valid && info.st_size == 0 && encrypt) {
      valid = RAND_bytes(master, sizeof master) == 1 &&
              ::write(fd, master, sizeof master) == sizeof master && fsync(fd) == 0;
    } else
      valid = valid && info.st_size == sizeof master && ::read(fd, master, sizeof master) == sizeof master;
    close(fd);
    require(valid, 503, "Credential master key is unavailable or has unsafe permissions");
    unsigned char iv[12], tag[16];
    std::string input = value;
    if (encrypt)
      require(RAND_bytes(iv, sizeof iv) == 1, 500, "Random generator failed");
    else {
      require(value.size() >= sizeof iv + sizeof tag, 503, "Invalid stored credential");
      memcpy(iv, value.data(), sizeof iv);
      memcpy(tag, value.data() + sizeof iv, sizeof tag);
      input = value.substr(sizeof iv + sizeof tag);
    }
    auto ctx = std::unique_ptr<EVP_CIPHER_CTX, decltype(&EVP_CIPHER_CTX_free)>(
        EVP_CIPHER_CTX_new(), EVP_CIPHER_CTX_free);
    std::string output(input.size() + 16, '\0');
    int n = 0, tail = 0;
    valid = ctx && EVP_CipherInit_ex(ctx.get(), EVP_aes_256_gcm(), nullptr, master, iv, encrypt) == 1 &&
            EVP_CipherUpdate(ctx.get(), (unsigned char *)output.data(), &n,
                             (const unsigned char *)input.data(), input.size()) == 1;
    if (!encrypt)
      valid = valid && EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_GCM_SET_TAG, sizeof tag, tag) == 1;
    valid = valid && EVP_CipherFinal_ex(ctx.get(), (unsigned char *)output.data() + n, &tail) == 1;
    if (encrypt)
      valid = valid && EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_GCM_GET_TAG, sizeof tag, tag) == 1;
    OPENSSL_cleanse(master, sizeof master);
    require(valid, 503, "Credential decryption failed; restore master key or re-enter API key");
    output.resize(n + tail);
    return encrypt ? std::string((char *)iv, sizeof iv) + std::string((char *)tag, sizeof tag) + output : output;
#endif
  }
public:
  ModelProviders(Store &store, fs::path data) : db(store), root(data) {
    db.exec("CREATE TABLE IF NOT EXISTS model_providers(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,"
            "metadata TEXT NOT NULL,credential TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS model_provider_selection(singleton INTEGER PRIMARY KEY CHECK(singleton=1),id TEXT NOT NULL);");
  }
  static json endpoint(json p) {
    auto url = field(p, "base_url", 2000);
    std::smatch m;
    require(std::regex_match(url, m, std::regex(R"((https?)://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:[0-9]{1,5})?(/[A-Za-z0-9._~%/-]*)?)")),
            400, "API 地址格式无效；不能含账号、查询参数或片段");
    if (m[3].matched) {
      int port = std::stoi(m[3].str().substr(1));
      require(port > 0 && port <= 65535, 400, "Invalid API port");
    }
    while (url.ends_with('/')) url.pop_back();
    auto protocol = p.value("protocol", "responses");
    for (auto suffix : {std::string("/chat/completions"), std::string("/responses")})
      if (url.ends_with(suffix)) {
        protocol = suffix == "/responses" ? "responses" : "chat_completions";
        url.resize(url.size() - suffix.size());
        break;
      }
    require(protocol == "responses" || protocol == "chat_completions", 400, "Invalid API protocol");
    p["base_url"] = url; p["protocol"] = protocol;
    return p;
  }
  static json pricing(const json &p) {
    require(!p.contains("pricing_mode") || p["pricing_mode"].is_string(), 400, "Invalid pricing mode");
    bool complete = p.contains("input_usd_per_million") && !p["input_usd_per_million"].is_null() &&
                    p.contains("output_usd_per_million") && !p["output_usd_per_million"].is_null();
    auto mode = p.value("pricing_mode", complete ? "custom" : "estimate");
    require(mode == "estimate" || mode == "custom", 400, "Invalid pricing mode");
    // Default planning rates are not a supplier quote or an actual billing ceiling.
    json result = {{"mode", mode}, {"input_usd_per_million", 10.0}, {"output_usd_per_million", 50.0}};
    if (mode == "custom") {
      require(complete, 400, "手动报价需要填写输入和输出单价；也可使用自动估算");
      for (auto k : {"input_usd_per_million", "output_usd_per_million"}) {
        require(p[k].is_number() && std::isfinite(p[k].get<double>()) && p[k].get<double>() >= 0,
                400, "Invalid model pricing");
        result[k] = p[k];
      }
    }
    return result;
  }
  std::string selected() {
    auto rows = db.rows("SELECT id FROM model_provider_selection WHERE singleton=1");
    return rows.empty() ? "" : rows[0]["id"].get<std::string>();
  }
  json get(const std::string &id) {
    auto rows = db.rows("SELECT metadata FROM model_providers WHERE id=?", {id});
    require(!rows.empty(), 404, "Model provider not found");
    return json::parse(rows[0]["metadata"].get<std::string>());
  }
  json list() {
    json out = json::array(); auto active = selected();
    for (auto row : db.rows("SELECT metadata FROM model_providers ORDER BY id")) {
      auto p = json::parse(row["metadata"].get<std::string>());
      p["active"] = p["id"] == active; out.push_back(p);
    }
    return {{"providers", out}, {"active_id", active}};
  }
  std::string key(const std::string &id) {
    require(!get(id).value("credential_unavailable", false), 503,
            "原系统密钥无法恢复，请在模型供应商中重新填写 API Key");
    auto rows = db.rows("SELECT credential FROM model_providers WHERE id=?", {id});
    require(!rows.empty(), 404, "Model provider not found");
    return crypt(decode_hex(rows[0]["credential"]), false);
  }
  json save(const json &j) {
    auto id = field(j, "id", 64);
    require(std::regex_match(id, std::regex("[a-f0-9]{32}")), 400, "Invalid provider ID");
    auto checked = endpoint(j);
    json p = {{"id", id}, {"name", field(j, "name", 200)}, {"model", j.value("model", "")},
              {"base_url", checked["base_url"]}, {"protocol", checked["protocol"]}, {"has_key", true}};
    require(p["model"].get<std::string>().size() <= 200, 400, "Model ID too long");
    for (auto k : {"input_usd_per_million", "output_usd_per_million"})
      if (j.contains(k) && !j[k].is_null()) {
        require(j[k].is_number() && std::isfinite(j[k].get<double>()) && j[k].get<double>() >= 0,
                400, "Invalid model pricing"); p[k] = j[k];
      }
    if (j.contains("pricing_mode")) p["pricing_mode"] = j["pricing_mode"];
    p["pricing_mode"] = pricing(p)["mode"];
    if (p["pricing_mode"] == "estimate") {
      p.erase("input_usd_per_million"); p.erase("output_usd_per_million");
    }
    auto old = db.rows("SELECT revision,metadata,credential FROM model_providers WHERE id=?", {id});
    int rev = old.empty() ? 0 : old[0]["revision"].get<int>();
    require(j.value("expected_revision", 0) == rev, 409, "供应商已变化，请刷新后重试");
    auto secret = j.value("api_key", "");
    require(secret.size() <= 8192 && secret.find_first_of("\r\n") == std::string::npos, 400, "Invalid API key");
    std::string sealed;
    if (secret.empty()) {
      require(!old.empty(), 400, "请填写 API Key");
      auto prior = json::parse(old[0]["metadata"].get<std::string>());
      require(!prior.value("credential_unavailable", false), 400,
              "原系统密钥无法恢复，请重新填写 API Key");
      require(prior["base_url"] == p["base_url"], 400, "修改请求地址时请重新填写 API Key");
      sealed = old[0]["credential"];
    } else {
      auto bytes = crypt(secret, true); sealed = hex((const unsigned char *)bytes.data(), bytes.size());
    }
    p["revision"] = rev + 1;
    Store::Tx tx(db);
    db.rows("INSERT INTO model_providers VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "revision=excluded.revision,metadata=excluded.metadata,credential=excluded.credential",
            {id, rev + 1, p.dump(), sealed});
    if (j.value("activate", false))
      db.rows("INSERT INTO model_provider_selection VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET id=excluded.id", {id});
    tx.commit(); return p;
  }
  json activate(const json &j) {
    auto id = field(j, "id", 64);
    auto p = get(id);
    require(p["revision"] == j.value("expected_revision", 0), 409, "供应商已变化，请刷新后重试");
    db.rows("INSERT INTO model_provider_selection VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET id=excluded.id", {id});
    return list();
  }
  json probe_request(const json &j) {
    auto p = endpoint(j); auto secret = j.value("api_key", "");
    if (secret.empty()) {
      auto id = field(j, "id", 64);
      auto prior = get(id);
      require(prior["base_url"] == p["base_url"], 400, "修改地址时请重新填写 API Key");
      secret = key(id);
    }
    require(!secret.empty() && secret.size() <= 8192 && secret.find_first_of("\r\n") == std::string::npos,
            400, "请填写有效的 API Key");
    p["api_key"] = secret; return p;
  }
  static json probe(const json &p) {
    auto url = p["base_url"].get<std::string>();
    auto slash = url.find('/', url.find("://") + 3);
    httplib::Client client(slash == std::string::npos ? url : url.substr(0, slash));
    client.set_connection_timeout(10); client.set_read_timeout(15); client.set_follow_location(false);
    if (p.contains("ca_bundle")) client.set_ca_cert_path(p["ca_bundle"].get<std::string>().c_str());
    std::string payload;
    auto response = client.Get((slash == std::string::npos ? "" : url.substr(slash)) + "/models",
                              {{"Authorization", "Bearer " + p["api_key"].get<std::string>()}},
                              [&](const char *bytes, size_t n) {
                                if (payload.size() + n > 2 * 1024 * 1024) return false;
                                payload.append(bytes, n); return true;
                              });
    require(bool(response), 502, "无法连接服务；核对地址、网络与证书");
    require(response->status == 200, 502, "模型列表请求返回 HTTP " + std::to_string(response->status) + "；可核对密钥或手动填写模型 ID");
    json body;
    try { body = json::parse(payload); } catch (...) { throw Error(502, "服务未返回有效的模型列表"); }
    require(body.contains("data") && body["data"].is_array(), 502, "服务不提供标准模型列表；请手动填写模型 ID");
    std::set<std::string> ids;
    for (auto item : body["data"])
      if (item.is_object() && item.contains("id") && item["id"].is_string()) {
        auto id = item["id"].get<std::string>(); if (!id.empty() && id.size() <= 200) ids.insert(id);
      }
    return {{"models", ids}, {"note", "列表可用；实际聊天能力需发送一条消息验证"}};
  }
};
}
