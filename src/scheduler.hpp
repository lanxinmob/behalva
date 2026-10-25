#pragma once
#include "service.hpp"

namespace pa {
inline json save_schedule(Service &s, const json &j) {
  auto kind = field(j, "kind", 32);
  require(kind == "research" || kind == "check_in", 400, "Unsupported schedule kind");
  auto interval = j.value("interval_seconds", int64_t(0));
  require(interval >= 900 && interval <= 366 * 86400, 400,
          "Interval must be 15 minutes to one year");
  auto next = j.value("next_at", now());
  require(next >= 0, 400, "Invalid scheduled timestamp");
  json schedule = {{"kind", kind},
                   {"title", field(j, "title", 20000)},
                   {"interval_seconds", interval},
                   {"next_at", next},
                   {"active", j.value("active", true)},
                   {"urls", j.value("urls", json::array())},
                   {"source_ids", j.value("source_ids", json::array())},
                   {"priority", j.value("priority", 1)}};
  require(schedule["urls"].is_array() && schedule["source_ids"].is_array(), 400,
          "Schedule sources must be arrays");
  if (j.contains("id"))
    schedule["id"] = j["id"];
  return s.db.put("schedules", schedule, j.value("expected_revision", 0));
}

inline void tick(Service &s) {
  std::lock_guard lock(s.db.mutex);
  s.recover_expired_tasks();
  for (auto schedule : s.db.list("schedules")) {
    if (!schedule.value("active", false) || schedule["next_at"].get<int64_t>() > now())
      continue;
    Store::Tx tx(s.db);
    auto id = schedule["id"].get<std::string>();
    // Missed intervals coalesce; an unanswered check-in never creates a
    // backlog.
    bool outstanding = false;
    for (auto task : s.db.list("tasks"))
      if (task.value("schedule_id", "") == id && task["state"] != "completed" &&
          task["state"] != "failed") {
        outstanding = true;
        break;
      }
    if (!outstanding) {
      auto task =
          s.create_task({{"title", schedule["title"]},
                         {"urls", schedule["urls"]},
                         {"source_ids", schedule["source_ids"]},
                         {"priority", schedule["priority"]},
                         {"request_id", "schedule:" + id + ":" +
                                            std::to_string(schedule["next_at"].get<int64_t>())}});
      task["schedule_id"] = id;
      if (schedule["kind"] == "check_in") {
        task["state"] = "waiting_input";
        task["result"] = {{"message", schedule["title"]},
                          {"origin", "approved recurring check-in"}};
      }
      s.db.put("tasks", task);
    }
    schedule["next_at"] = now() + schedule["interval_seconds"].get<int64_t>();
    s.db.put("schedules", schedule);
    tx.commit();
  }
}
} // namespace pa
