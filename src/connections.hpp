#pragma once
#include "service.hpp"
#include <cstdlib>
#include <map>

namespace pa {
// Liveness is observed by this process only. It resets on restart so a previous
// run can never make a connection look healthy now.
class ConnectionMonitor {
  std::mutex mutex;
  struct Seen {
    int64_t at = 0;
    bool tailnet = false;
  };
  std::map<std::string, Seen> devices;
  int64_t worker_at = 0, qq_event_at = 0, qq_rejected_at = 0, revoked_at = 0;
  std::string revoked_name;

public:
  const int64_t started_at = now();
  // The Host header is client-controlled: display hint only, never authorization.
  static bool tailnet_host(std::string host) {
    host = ascii_lower(host);
    if (auto colon = host.rfind(':'); colon != std::string::npos && host.find(']') == std::string::npos)
      host.resize(colon);
    while (host.ends_with('.'))
      host.pop_back();
    return host.ends_with(".ts.net");
  }
  void device(const std::string &id, const std::string &host, const std::string &forwarded) {
    std::lock_guard lock(mutex);
    devices[id] = {now(), tailnet_host(host) || tailnet_host(forwarded)};
  }
  void revoked_device(const std::string &name) {
    std::lock_guard lock(mutex);
    revoked_at = now();
    revoked_name = name;
  }
  void worker() {
    std::lock_guard lock(mutex);
    worker_at = now();
  }
  void qq_event() {
    std::lock_guard lock(mutex);
    qq_event_at = now();
  }
  void qq_rejected() {
    std::lock_guard lock(mutex);
    qq_rejected_at = now();
  }
  json snapshot() {
    std::lock_guard lock(mutex);
    json seen = json::object();
    for (auto &[id, s] : devices)
      seen[id] = {{"at", s.at}, {"tailnet", s.tailnet}};
    return {{"devices", seen},          {"worker_at", worker_at},
            {"qq_event_at", qq_event_at}, {"qq_rejected_at", qq_rejected_at},
            {"revoked_at", revoked_at},   {"revoked_name", revoked_name}};
  }
};

namespace connection_detail {
constexpr int64_t worker_stale_seconds = 30;
constexpr int64_t check_claim_seconds = 600;
constexpr int64_t check_answer_seconds = 120;

inline json with_state(json c, const std::string &state, const std::string &detail,
                       int64_t checked_at = 0) {
  c["state"] = state;
  c["detail"] = detail;
  if (checked_at)
    c["checked_at"] = checked_at;
  return c;
}

inline std::string http_reason(int code) {
  auto text = "供应商返回 HTTP " + std::to_string(code);
  if (code == 401 || code == 403)
    return text + "；请核对 API Key 与访问权限。";
  if (code == 404)
    return text + "；请核对请求地址与模型 ID。";
  if (code == 429)
    return text + "；额度或请求频率受限。";
  return text + "。";
}

inline json model(Service &s) {
  json c = {{"id", "model"}};
  auto p = s.provider_config();
  if (p.empty())
    return with_state(c, "not_configured", "尚未添加模型供应商；不会产生付费调用。");
  c["provider"] = p.value("name", "启动配置");
  c["model"] = p.value("model", "");
  c["endpoint"] = p.value("base_url", "");
  if (p.value("credential_unavailable", false))
    return with_state(c, "error", "原系统密钥无法恢复；请编辑供应商并重新填写 API Key。");
  if (c["model"].get<std::string>().empty())
    return with_state(c, "not_configured", "已添加供应商，尚未选择模型。");
  try {
    ModelProviders::pricing(p);
  } catch (const Error &) {
    return with_state(c, "error", "费用配置无效；请检查手动报价或改用自动估算。");
  }
  if (!p.contains("id")) {
    auto env = p.value("api_key_env", "PA_API_KEY");
    auto value = std::getenv(env.c_str());
    if (!value || !*value)
      return with_state(c, "error", "启动配置所需的密钥环境变量未设置。");
  }
  auto provider_id = p.value("id", "legacy");
  auto revision = p.value("revision", 0);
  auto model_id = c["model"].get<std::string>(), endpoint = c["endpoint"].get<std::string>();
  // Timestamps are whole seconds; the event sequence orders runs finished in the same second.
  auto rows = s.db.rows("SELECT r.data FROM resources r JOIN events e ON e.kind=r.kind AND e.id=r.id "
                        "AND e.rev=r.rev WHERE r.kind='model_runs' ORDER BY e.seq DESC");
  for (auto &row : rows) {
    auto run = json::parse(row["data"].get<std::string>());
    if (run.value("provider_id", "") != provider_id || run.value("provider_revision", 0) != revision ||
        run.value("model", "") != model_id || run.value("base_url", "") != endpoint)
      continue;
    auto at = run.value("updated_at", int64_t(0));
    auto state = run.value("state", "");
    if (state == "succeeded")
      return with_state(c, "verified", "最近一次真实回复成功。", at);
    if (state != "unknown")
      continue;
    auto notice = run.value("notice", "");
    const std::string http = "Provider HTTP ";
    if (notice.starts_with(http)) {
      int code = std::atoi(notice.c_str() + http.size());
      return with_state(c, "error",
                        code >= 200 && code < 300 ? "供应商已响应，但结果不含可用文本或未能保存。"
                                                  : http_reason(code),
                        at);
    }
    if (notice.starts_with("Model response unavailable"))
      return with_state(c, "error", "上次调用没有收到供应商响应；请核对地址、网络与证书。", at);
    // A restart interrupted the call; it says nothing about the endpoint itself.
  }
  return with_state(c, "unverified", "已配置，尚无真实回复；发送一条聊天消息后核实。");
}

inline json gmail_check_view(const json &record) {
  json v = record.is_object() ? record : json::object();
  auto state = v.value("state", "");
  if (state == "requested" && now() - v.value("requested_at", int64_t(0)) > check_claim_seconds)
    v["state"] = "expired";
  if (state == "checking" && now() - v.value("claimed_at", int64_t(0)) > check_answer_seconds)
    v["state"] = "unanswered";
  v.erase("attempt_id");
  return v;
}

inline json gmail(Service &s) {
  json c = {{"id", "gmail"}};
  json record = nullptr;
  try {
    record = s.db.get("connection_checks", "gmail");
  } catch (const Error &) {
  }
  c["check"] = gmail_check_view(record);
  auto configured = s.config.contains("gmail_credentials") && s.config["gmail_credentials"].is_string()
                        ? s.config["gmail_credentials"].get<std::string>()
                        : "";
  if (configured.empty())
    return with_state(c, "not_configured", "尚未完成 Gmail 授权；可以准备邮件草稿，但无法发送。");
  auto path = path_from_utf8(configured);
  std::error_code missing;
  if (!fs::is_regular_file(path, missing))
    return with_state(c, "error", "已设置授权文件位置，但找不到文件；请在电脑重新运行 Gmail 授权。");
  // Only the shape of the credential is inspected; nothing from it is returned.
  try {
    auto credential = json::parse(read(path));
    auto refresh = credential.contains("refresh_token") && credential["refresh_token"].is_string() &&
                   !credential["refresh_token"].get<std::string>().empty();
    if (!refresh)
      return with_state(c, "error", "授权文件缺少 refresh token；请重新授权。");
    if (credential.contains("scopes") && credential["scopes"].is_array()) {
      bool send = false;
      for (auto &scope : credential["scopes"])
        send = send || scope == "https://www.googleapis.com/auth/gmail.send";
      if (!send)
        return with_state(c, "error", "授权未包含发送邮件权限；请重新授权。");
    }
  } catch (const std::exception &) {
    return with_state(c, "error", "授权文件无法读取或格式无效；请重新授权。");
  }
  c["verifiable"] = true;
  int64_t sent_at = 0;
  for (auto &o : s.db.list("operations"))
    if (o.value("kind", "") == "gmail" && o.value("state", "") == "succeeded") {
      sent_at = o.value("updated_at", int64_t(0));
      break;
    }
  auto result = record.is_object() ? record.value("result", json::object()) : json::object();
  auto checked_at = result.value("checked_at", int64_t(0));
  if (result.contains("account"))
    c["account"] = result["account"];
  if (checked_at >= sent_at && checked_at) {
    if (result.value("state", "") == "verified")
      return with_state(c, "verified", "已读取账号资料，授权有效。", checked_at);
    return with_state(c, "error", result.value("reason", "验证失败。"), checked_at);
  }
  if (sent_at)
    return with_state(c, "verified", "最近一封获批邮件已核实发出。", sent_at);
  return with_state(c, "unverified", "已找到授权文件，尚未验证；验证只读取账号资料，不发送邮件。");
}

inline json qq(Service &s, const json &seen) {
  json c = {{"id", "qq"}};
  int active = 0;
  for (auto &g : s.db.list("groups"))
    active += g.value("active", false) ? 1 : 0;
  c["active_groups"] = active;
  auto last = s.db.rows("SELECT MAX(updated) AS at FROM resources WHERE kind='sources' AND "
                        "json_extract(data,'$.kind')='qq'");
  if (!last.empty() && last[0]["at"].is_number())
    c["last_message_at"] = last[0]["at"];
  auto event_at = seen["qq_event_at"].get<int64_t>(), rejected_at = seen["qq_rejected_at"].get<int64_t>();
  if (rejected_at > event_at)
    return with_state(c, "error", "OneBot 连接器的令牌被拒绝；请让连接器使用私人目录中的 qq.token。",
                      rejected_at);
  if (!active)
    return with_state(c, "not_configured",
                      event_at ? "连接器已在推送，但尚未授权任何群，消息不会保存。"
                               : "尚未授权任何 QQ 群。");
  if (event_at)
    return with_state(c, "verified", "本次启动后已收到 OneBot 连接器推送。", event_at);
  return with_state(c, "unverified", "已授权 " + std::to_string(active) +
                                         " 个群；本次启动后尚未收到 OneBot 连接器推送。");
}

inline json phone(Service &s, const json &seen, const json &transport) {
  json c = {{"id", "phone"}, {"transport", transport}, {"devices", json::array()}};
  int active = 0;
  int64_t latest = 0;
  std::string latest_name;
  bool latest_tailnet = false;
  for (auto &d : s.devices()) {
    auto id = d["id"].get<std::string>();
    auto name = d.value("name", "");
    bool on = d.value("active", false);
    json item = {{"id", id}, {"name", name}, {"active", on}};
    if (seen["devices"].contains(id)) {
      auto at = seen["devices"][id]["at"].get<int64_t>();
      bool tailnet = seen["devices"][id]["tailnet"].get<bool>();
      item["last_seen_at"] = at;
      item["tailnet"] = tailnet;
      if (on && at > latest) {
        latest = at;
        latest_name = name;
        latest_tailnet = tailnet;
      }
    }
    active += on ? 1 : 0;
    c["devices"].push_back(item);
  }
  auto revoked_at = seen["revoked_at"].get<int64_t>();
  if (revoked_at > latest)
    return with_state(c, "error",
                      "已撤销的设备「" + seen["revoked_name"].get<std::string>() +
                          "」仍在尝试连接；请在该手机重新导入连接配置。",
                      revoked_at);
  if (!active)
    return with_state(c, "not_configured", "尚未配对手机。");
  if (latest)
    return with_state(c, "verified",
                      "「" + latest_name + "」本次启动后已连接" +
                          (latest_tailnet ? "，经由 Tailscale HTTPS 地址。" : "。"),
                      latest);
  return with_state(c, "unverified", "已配对 " + std::to_string(active) +
                                         " 台设备；本次启动后还没有手机连接过。");
}

inline json worker(const json &seen) {
  json c = {{"id", "worker"}};
  auto at = seen["worker_at"].get<int64_t>();
  if (at && now() - at <= worker_stale_seconds)
    return with_state(c, "verified", "运行中；聊天回复、邮件发送与连接验证由它执行。", at);
  if (at)
    return with_state(c, "error", "已停止响应；请检查私人目录中的 logs/worker.log。", at);
  return with_state(c, "unverified", "本次启动后尚未连接；聊天回复与邮件发送需要它运行。");
}
} // namespace connection_detail

inline json connections_report(Service &s, ConnectionMonitor &monitor, const json &transport) {
  auto seen = monitor.snapshot();
  namespace d = connection_detail;
  return {{"generated_at", now()},
          {"service_started_at", monitor.started_at},
          {"connections", json::array({d::model(s), d::gmail(s), d::qq(s, seen),
                                       d::phone(s, seen, transport), d::worker(seen)})}};
}

// A verification is one explicit attempt: a lost answer is reported, never re-run.
inline json request_gmail_check(Service &s) {
  require(s.config.contains("gmail_credentials") && s.config["gmail_credentials"].is_string() &&
              !s.config["gmail_credentials"].get<std::string>().empty(),
          409, "尚未配置 Gmail 授权文件");
  json record = {{"id", "gmail"}};
  try {
    record = s.db.get("connection_checks", "gmail");
  } catch (const Error &) {
  }
  auto view = connection_detail::gmail_check_view(record);
  if (view.value("state", "") == "requested" || view.value("state", "") == "checking")
    return view;
  record["state"] = "requested";
  record["requested_at"] = now();
  record.erase("attempt_id");
  record.erase("claimed_at");
  return connection_detail::gmail_check_view(s.db.put("connection_checks", record));
}

inline json claim_gmail_check(Service &s) {
  auto record = s.db.get("connection_checks", "gmail");
  require(connection_detail::gmail_check_view(record).value("state", "") == "requested", 409,
          "No pending Gmail verification");
  record["state"] = "checking";
  record["attempt_id"] = uid();
  record["claimed_at"] = now();
  return s.db.put("connection_checks", record);
}

inline json finish_gmail_check(Service &s, const json &j) {
  auto record = s.db.get("connection_checks", "gmail");
  require(record.value("state", "") == "checking" &&
              record.value("attempt_id", "") == field(j, "attempt_id", 64),
          409, "Stale Gmail verification attempt");
  auto state = field(j, "state", 32);
  require(state == "verified" || state == "error", 400, "Invalid verification result");
  json result = {{"state", state}, {"checked_at", now()}};
  if (state == "verified")
    result["account"] = field(j, "account", 320);
  else
    result["reason"] = field(j, "reason", 300);
  record["state"] = "done";
  record["result"] = result;
  record.erase("attempt_id");
  return connection_detail::gmail_check_view(s.db.put("connection_checks", record));
}
} // namespace pa
