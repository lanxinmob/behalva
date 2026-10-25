#pragma once
#include "service.hpp"
#include <deque>

namespace pa {
// Input and screenshots stay in memory. A lost response must not replay a
// click.
class BrowserBridge {
  std::mutex mutex;
  std::deque<json> commands;
  json snapshot = {{"status", "offline"}};
  std::string session, pending;
  int64_t updated = 0, generation = 0;

public:
  json view() {
    std::lock_guard lock(mutex);
    auto v = snapshot;
    v["session_id"] = session;
    v["generation"] = generation;
    v["controller"] = "owner";
    v["pending"] = !pending.empty();
    v["connected"] = now() - updated < 20;
    return v;
  }
  json command(const json &j) {
    std::lock_guard lock(mutex);
    require(!session.empty() && now() - updated < 20, 503,
            "Browser adapter is offline");
    require(j.value("session_id", "") == session &&
                j.value("generation", int64_t(-1)) == generation,
            409, "Browser view changed; refresh before acting");
    require(pending.empty(), 409,
            "Previous browser action is pending; do not repeat it");
    auto action = field(j, "action", 32);
    require(std::set<std::string>{"navigate", "click", "text", "key", "scroll",
                                  "refresh"}
                .contains(action),
            400, "Unsupported browser action");
    auto command = j;
    command["frame_hash"] = hash(snapshot.value("image_base64", ""));
    if (action == "navigate")
      require(field(j, "url", 8192).starts_with("https://"), 400,
              "Browser navigation requires HTTPS");
    if (action == "text")
      field(j, "text", 10000);
    if (action == "key")
      require(
          std::set<std::string>{"Enter", "Tab", "Backspace", "Escape"}.contains(
              field(j, "key", 32)),
          400, "Unsupported key");
    if (action == "click")
      require(j.value("x", -1) >= 0 && j.value("x", 99999) < 1024 &&
                  j.value("y", -1) >= 0 && j.value("y", 99999) < 768,
              400, "Point outside viewport");
    if (action == "scroll")
      require(j.value("dy", 0) >= -768 && j.value("dy", 0) <= 768, 400,
              "Scroll exceeds viewport");
    pending = uid();
    command["id"] = pending;
    commands.push_back(command);
    return {{"id", pending}, {"state", "queued"}};
  }
  json poll() {
    std::lock_guard lock(mutex);
    if (commands.empty())
      return {{"command", nullptr}};
    auto next = commands.front();
    commands.pop_front();
    if (next["generation"] != generation) {
      pending.clear();
      snapshot["error"] =
          "Page changed before execution; review the new frame and act again";
      return {{"command", nullptr}};
    }
    return {{"command", next}};
  }
  json publish(const json &j) {
    std::lock_guard lock(mutex);
    auto incoming = field(j, "session_id", 64);
    auto image = field(j, "image_base64", 6 * 1024 * 1024);
    unbase64(image);
    if (j.contains("command_id"))
      require(incoming == session && j["command_id"] == pending, 409,
              "Browser action is stale");
    if (incoming != session) {
      session = incoming;
      commands.clear();
      pending.clear();
      ++generation;
    }
    if (j.contains("command_id")) {
      require(j["command_id"] == pending, 409, "Browser action is stale");
      pending.clear();
      ++generation;
    }
    if (snapshot.value("image_base64", "") != image)
      ++generation;
    snapshot = {{"status", j.value("status", "ready")},
                {"image_base64", image},
                {"url", j.value("url", "")},
                {"title", j.value("title", "")},
                {"error", j.value("error", "")},
                {"width", 1024},
                {"height", 768}};
    updated = now();
    return {{"ok", true}};
  }
};
} // namespace pa
