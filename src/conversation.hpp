#pragma once
#include "service.hpp"
#include <regex>
namespace pa {
inline json conversation_history(Service &s, const std::string &id, int through) {
  json messages = json::array();
  for (auto &row :
       s.db.rows("SELECT data FROM resources WHERE kind='messages' AND "
                 "json_extract(data,'$.conversation_id')=? AND "
                 "json_extract(data,'$.sequence')<=? ORDER BY json_extract(data,'$.sequence')",
                 {id, through})) {
    auto m = json::parse(row["data"].get<std::string>());
    messages.push_back({{"role", m["role"]}, {"content", m["body"]}});
  }
  require(messages.dump().size() <= 120000, 413,
          "对话已达到上下文上限，历史仍保留。请新建对话并写明需要沿用的结论。");
  return messages;
}

inline json post_message(Service &s, const json &j) {
  auto id = field(j, "conversation_id", 64), body = field(j, "body", 20000);
  require(std::regex_match(id, std::regex("[a-f0-9]{32}")), 400, "Invalid conversation id");
  field(j, "request_id", 100);
  require(j.value("expected_sequence", -1) >= 0, 400, "Expected sequence required");
  Store::Tx tx(s.db);
  auto found = s.db.rows("SELECT data FROM resources WHERE kind='conversations' AND id=?", {id});
  auto c = found.empty() ? json{{"id", id}, {"title", body}, {"sequence", 0}}
                         : s.db.get("conversations", id);
  require(c["sequence"] == j["expected_sequence"], 409,
          "对话已有新消息，请同步后重新发送。原输入仍可保留。");
  if (c.contains("last_task_id")) {
    auto previous = s.db.get("tasks", c["last_task_id"]);
    require(previous["state"] == "completed", 409,
            "请先处理上一轮回复；等待中的调用不会自动重复。也可以新建对话。");
  }
  auto history = conversation_history(s, id, c["sequence"]);
  history.push_back({{"role", "user"}, {"content", body}});
  require(history.dump().size() <= 120000, 413, "对话过长，请新建对话，旧记录仍保留。");
  auto task = s.create_task(
      {{"title", body}, {"request_id", "chat:" + j["request_id"].get<std::string>()}});
  task["kind"] = "chat";
  task["conversation_id"] = id;
  task["through_sequence"] = c["sequence"].get<int>() + 1;
  task = s.db.put("tasks", task);
  auto message = s.db.put("messages",
                          {{"conversation_id", id},
                           {"sequence", task["through_sequence"]},
                           {"role", "user"},
                           {"body", body},
                           {"task_id", task["id"]}},
                          0);
  c["sequence"] = task["through_sequence"];
  c["last_task_id"] = task["id"];
  s.db.put("conversations", c);
  tx.commit();
  return {{"message", message}, {"task_id", task["id"]}, {"conversation_id", id}};
}

inline json conversation_task(Service &s, const std::string &id, const json &j) {
  field(j, "request_id", 128);
  auto c = s.db.get("conversations", id);
  require(c["sequence"] == j.value("expected_sequence", -1), 409,
          "对话已变化，请同步后核对要创建的任务。");
  auto kind = field(j, "kind", 32);
  require(kind == "research" || kind == "feature", 400, "Invalid task kind");
  auto history = conversation_history(s, id, c["sequence"]);
  Store::Tx tx(s.db);
  auto task = s.create_task(j);
  task["kind"] = kind;
  task["conversation_id"] = id;
  task["conversation_context"] = history;
  if (kind == "feature") {
    task["state"] = "waiting_input";
    task["result"] = {{"message", "功能需求已保存，等待开发安排。代码启用须经过验证、两名独立 "
                                  "Agent 审查和你的确认。当前没有自动代码执行器。"}};
  }
  task = s.db.put("tasks", task);
  tx.commit();
  return task;
}
} // namespace pa
