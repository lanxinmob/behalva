#define CPPHTTPLIB_OPENSSL_SUPPORT
#include "connections.hpp"
#include "browser.hpp"
#include "fetch.hpp"
#include "scheduler.hpp"
#include <functional>
#include <iostream>
#include <thread>
using namespace pa;
int main(int argc, char **argv) {
  try {
    fs::path data = "runtime", web = "web", config_path;
    std::string host = "127.0.0.1", cert, key;
    int port = 8765;
    json config = json::object();
    for (int i = 1; i < argc; i++) {
      std::string a = argv[i];
      require(i + 1 < argc, 400, "Argument value missing");
      std::string v = argv[++i];
      if (a == "--data")
        data = path_from_utf8(v);
      else if (a == "--config") {
        config_path = path_from_utf8(v);
        config = json::parse(read(config_path));
      }
      else if (a == "--port")
        port = std::stoi(v);
      else if (a == "--host")
        host = v;
      else if (a == "--web")
        web = path_from_utf8(v);
      else if (a == "--cert")
        cert = v;
      else if (a == "--key")
        key = v;
      else
        throw Error(400, "Unknown option");
    }
    require(host == "127.0.0.1" || (!cert.empty() && !key.empty()), 400,
            "Non-loopback binding requires TLS certificate and key");
    fs::create_directories(data);
    auto secret = [&](const std::string &name) {
      auto path = data / name;
      if (!fs::exists(path))
        write(path, uid() + uid());
      return read(path);
    };
    InstanceLock instance(data / "instance.lock");
    auto owner = secret("owner.token"), worker = secret("worker.token"), qq = secret("qq.token");
    Service service(data, config);
    BrowserBridge browser;
    ConnectionMonitor monitor;
    std::jthread scheduler([&](std::stop_token stop) {
      while (!stop.stop_requested()) {
        try {
          tick(service);
        } catch (const std::exception &e) {
          std::cerr << "Scheduler failed: " << e.what() << "\n";
        }
        for (int i = 0; i < 20 && !stop.stop_requested(); ++i)
          std::this_thread::sleep_for(std::chrono::milliseconds(250));
      }
    });
    std::unique_ptr<httplib::Server> server;
    if (!cert.empty())
      server = std::make_unique<httplib::SSLServer>(cert.c_str(), key.c_str());
    else
      server = std::make_unique<httplib::Server>();
    auto &http = *server;
    require(http.is_valid(), 500, "HTTP/TLS initialization failed");
    http.new_task_queue = [] { return new httplib::ThreadPool(4); };
    http.set_payload_max_length(8 * 1024 * 1024);
    http.set_read_timeout(15);
    http.set_write_timeout(30);
    auto send = [](httplib::Response &r, const json &v) {
      r.set_content(v.dump(-1, ' ', false, json::error_handler_t::replace),
                    "application/json; charset=utf-8");
      r.set_header("Cache-Control", "no-store");
      r.set_header("X-Content-Type-Options", "nosniff");
    };
    using Fn = std::function<json(const httplib::Request &, const json &, bool)>;
    auto route = [&](std::string method, std::string path, bool owner_only, Fn fn,
                     bool locked = true) {
      auto cb = [&, owner_only, fn, locked](const httplib::Request &q, httplib::Response &r) {
        try {
          auto auth = q.get_header_value("Authorization");
          bool isowner = equal_secret(auth, "Bearer " + owner),
               isworker = equal_secret(auth, "Bearer " + worker);
          if (!isowner && !isworker) {
            std::lock_guard authlock(service.db.mutex);
            auto device = service.device_for(auth);
            if (device.is_object() && device.value("active", false)) {
              isowner = true;
              monitor.device(device["id"], q.get_header_value("Host"),
                             q.get_header_value("X-Forwarded-Host"));
            } else if (device.is_object())
              monitor.revoked_device(device.value("name", ""));
          }
          if (isworker)
            monitor.worker();
          require(isowner || isworker, 401, "Authentication required");
          require(!owner_only || isowner, 403, "Owner approval required");
          // API tokens are not accepted from URLs, cookies or cross-origin
          // forms.
          if (q.method != "GET")
            require(q.get_header_value("Content-Type").starts_with("application/json"), 415,
                    "JSON content type required");
          json j = q.body.empty() ? json::object() : json::parse(q.body);
          require(j.is_object(), 400, "JSON object required");
          std::unique_lock<std::recursive_mutex> lock(service.db.mutex, std::defer_lock);
          if (locked)
            lock.lock();
          const bool replayable =
              locked && q.method != "GET" &&
              (q.path == "/api/tasks" || q.path == "/api/drafts" || q.path == "/api/goals" ||
               q.path == "/api/rules" || q.path == "/api/operations" || q.path == "/api/tables" ||
               q.path == "/api/attachments" || q.path == "/api/documents" ||
               q.path == "/api/schedules" || q.path == "/api/messages" ||
               (q.path.starts_with("/api/conversations/") && q.path.ends_with("/tasks")) ||
               q.path.starts_with("/api/drafts/") ||
               (q.path.starts_with("/api/tasks/") && q.path.ends_with("/resume")));
          auto request_key = j.value("request_id", "");
          if (replayable && !request_key.empty()) {
            require(request_key.size() <= 128, 400, "Request id too long");
            auto id = hash(auth + ":" + request_key),
                 digest = hash(q.method + ":" + q.path + ":" + j.dump());
            auto prior = service.db.rows("SELECT data FROM resources WHERE "
                                         "kind='http_requests' AND id=?",
                                         {id});
            if (!prior.empty()) {
              auto saved = json::parse(prior[0]["data"].get<std::string>());
              require(saved["digest"] == digest, 409, "Request id reused with different content");
              r.status = saved.value("status", 200);
              send(r, saved["response"]);
              return;
            }
            Store::Tx tx(service.db);
            json result;
            int status = 200;
            try {
              result = fn(q, j, isowner);
            } catch (const Error &e) {
              if (e.status != 409 || !e.details.contains("conflict"))
                throw;
              status = e.status;
              result = {{"error", e.what()}, {"details", e.details}};
            }
            service.db.put(
                "http_requests",
                {{"id", id}, {"digest", digest}, {"status", status}, {"response", result}}, 0);
            tx.commit();
            r.status = status;
            send(r, result);
          } else
            send(r, fn(q, j, isowner));
        } catch (const Error &e) {
          r.status = e.status;
          send(r, {{"error", e.what()}, {"details", e.details}});
        } catch (const json::exception &) {
          r.status = 400;
          send(r, {{"error", "Invalid JSON or field type"}});
        } catch (const std::exception &e) {
          std::cerr << "Request failed: " << e.what() << "\n";
          r.status = 500;
          send(r, {{"error", "Internal error; inspect local service log"}});
        }
      };
      if (method == "GET")
        http.Get(path, cb);
      else if (method == "PUT")
        http.Put(path, cb);
      else
        http.Post(path, cb);
    };
    http.Get("/health",
             [&](const auto &, auto &r) { send(r, {{"status", "ok"}, {"version", "0.1.0-dev"}}); });
    auto computer_owner = [&](const httplib::Request &q) {
      require(equal_secret(q.get_header_value("Authorization"), "Bearer " + owner),
              403, "模型供应商管理仅限电脑管理令牌");
    };
    route("GET", "/api/providers", true, [&](const auto &q, const auto &, bool) {
      computer_owner(q); return service.providers.list();
    });
    route("POST", "/api/providers", true, [&](const auto &q, const auto &j, bool) {
      computer_owner(q); return service.providers.save(j);
    });
    route("POST", "/api/providers/activate", true, [&](const auto &q, const auto &j, bool) {
      computer_owner(q); return service.providers.activate(j);
    });
    route("POST", "/api/providers/models", true, [&](const auto &q, const auto &j, bool) {
      computer_owner(q);
      json p;
      {
        std::lock_guard lock(service.db.mutex);
        p = service.providers.probe_request(j);
        p.erase("ca_bundle");
        if (service.config.contains("ca_bundle")) p["ca_bundle"] = service.config["ca_bundle"];
      }
      return ModelProviders::probe(p);
    }, false);
    route("POST", "/api/devices", true,
          [&](const auto &, const auto &j, bool) { return service.pair_device(j); });
    route("POST", R"(/api/devices/([a-f0-9]+)/revoke)", true,
          [&](const auto &q, const auto &, bool) { return service.revoke_device(q.matches[1]); });
    route(
        "GET", "/api/browser/view", true,
        [&](const auto &, const auto &, bool) { return browser.view(); }, false);
    route(
        "POST", "/api/browser/command", true,
        [&](const auto &, const auto &j, bool) { return browser.command(j); }, false);
    route(
        "POST", "/api/browser/poll", false,
        [&](const auto &, const auto &, bool) { return browser.poll(); }, false);
    route(
        "POST", "/api/browser/publish", false,
        [&](const auto &, const auto &j, bool) { return browser.publish(j); }, false);
    route("POST", "/api/schedules", true,
          [&](const auto &, const auto &j, bool) { return save_schedule(service, j); });
    route("POST", R"(/api/model-runs/([a-f0-9]+)/retry)", true,
          [&](const auto &q, const auto &j, bool) {
            return service.approve_model_retry(q.matches[1], j);
          });
    route("POST", R"(/api/sources/([a-f0-9]+)/availability)", false,
          [&](const auto &q, const auto &j, bool) {
            return service.source_availability(q.matches[1], j);
          });
    route("GET", "/api/setup", true, [&](const auto &, const auto &, bool) {
      auto fingerprint = data / "server-fingerprint.txt";
      return json{{"listen_host", host}, {"port", port}, {"tls", !cert.empty()},
                  {"data_path", path_utf8(fs::absolute(data))},
                  {"config_path", config_path.empty() ? "" : path_utf8(fs::absolute(config_path))},
                  {"fingerprint_path", path_utf8(fs::absolute(fingerprint))},
                  {"fingerprint", fs::exists(fingerprint) ? read(fingerprint) : ""},
                  {"model_configured", service.model_configured()}};
    });
    route("GET", "/api/connections", true, [&](const auto &, const auto &, bool) {
      return connections_report(service, monitor,
                                {{"listen_host", host}, {"port", port}, {"tls", !cert.empty()}});
    });
    route("POST", "/api/connections/gmail/check", true,
          [&](const auto &, const auto &, bool) { return request_gmail_check(service); });
    route("POST", "/api/connections/gmail/check/claim", false,
          [&](const auto &, const auto &, bool isowner) {
            require(!isowner, 403, "Only the local worker performs verification");
            return claim_gmail_check(service);
          });
    route("POST", "/api/connections/gmail/check/result", false,
          [&](const auto &, const auto &j, bool isowner) {
            require(!isowner, 403, "Only the local worker reports verification");
            return finish_gmail_check(service, j);
          });
    route("GET", "/api/state", false, [&](const auto &, const auto &, bool) {
      service.sync_rule_files();
      return service.state();
    });
    route(
        "POST", "/api/web/fetch", false,
        [&](const auto &, const auto &j, bool) { return fetch_source(service, j); }, false);
    route("POST", "/api/attachments", false,
          [&](const auto &, const auto &j, bool) { return service.attachment(j); });
    route("POST", R"(/api/attachments/([a-f0-9]+)/extraction)", false,
          [&](const auto &q, const auto &j, bool) {
            return service.attachment_extraction(q.matches[1], j);
          });
    route("GET", R"(/api/attachments/([a-f0-9]+))", false, [&](const auto &q, const auto &, bool) {
      return service.db.get("attachments", q.matches[1]);
    });
    route("POST", R"(/api/conflicts/([a-f0-9]+)/resolve)", true,
          [&](const auto &q, const auto &j, bool) {
            return service.resolve_conflict(q.matches[1], j);
          });
    http.Post("/api/qq/events", [&](const auto &q, auto &r) {
      try {
        if (!equal_secret(q.get_header_value("Authorization"), "Bearer " + qq)) {
          monitor.qq_rejected();
          throw Error(401, "QQ connector authentication required");
        }
        monitor.qq_event();
        auto j = json::parse(q.body);
        j["kind"] = "qq";
        std::lock_guard lock(service.db.mutex);
        send(r, service.ingest(j));
      } catch (const Error &e) {
        r.status = e.status;
        send(r, {{"error", e.what()}});
      } catch (...) {
        r.status = 400;
        send(r, {{"error", "Invalid QQ event"}});
      }
    });
    route("POST", "/api/tasks", true,
          [&](const auto &, const auto &j, bool) { return service.create_task(j); });
    route("POST", "/api/messages", true,
          [&](const auto &, const auto &j, bool) { return post_message(service, j); });
    route("POST", R"(/api/conversations/([a-f0-9]+)/tasks)", true,
          [&](const auto &q, const auto &j, bool) {
            return conversation_task(service, q.matches[1], j);
          });
    route("POST", R"(/api/tasks/([a-f0-9]+)/claim)", false,
          [&](const auto &q, const auto &, bool) { return service.claim_task(q.matches[1]); });
    route("POST", R"(/api/tasks/([a-f0-9]+)/heartbeat)", false,
          [&](const auto &q, const auto &j, bool) { return service.heartbeat(q.matches[1], j); });
    route("POST", R"(/api/tasks/([a-f0-9]+)/result)", false,
          [&](const auto &q, const auto &j, bool) { return service.task_result(q.matches[1], j); });
    route("POST", R"(/api/tasks/([a-f0-9]+)/resume)", true,
          [&](const auto &q, const auto &j, bool) { return service.resume_task(q.matches[1], j); });
    route("POST", "/api/drafts", false,
          [&](const auto &, const auto &j, bool) { return service.draft(j); });
    route("PUT", R"(/api/drafts/([a-f0-9]+))", true,
          [&](const auto &q, const auto &j, bool) { return service.draft(j, q.matches[1]); });
    route("POST", "/api/groups", true,
          [&](const auto &, const auto &j, bool) { return service.group(j); });
    route("POST", "/api/grants", true,
          [&](const auto &, const auto &j, bool) { return service.grant(j); });
    route("POST", "/api/documents", false, [&](const auto &, const auto &j, bool) {
      require(j.value("kind", "") == "file" || j.value("kind", "") == "shared", 403,
              "Public and QQ sources require verified connector endpoints");
      return service.ingest(j);
    });
    route("GET", R"(/api/versions/([a-f0-9]+))", false,
          [&](const auto &q, const auto &, bool) { return service.version(q.matches[1]); });
    route("GET", "/api/search", false, [&](const auto &q, const auto &, bool) {
      return service.search(q.get_param_value("q"));
    });
    route("POST", "/api/context", false,
          [&](const auto &, const auto &j, bool) { return service.context(j.at("source_ids")); });
    route("POST", "/api/rules", false,
          [&](const auto &, const auto &j, bool) { return service.propose_rule(j); });
    route(
        "POST", R"(/api/rules/([a-f0-9]+)/approve)", true,
        [&](const auto &q, const auto &j, bool) { return service.approve_rule(q.matches[1], j); });
    route("POST", "/api/operations", false,
          [&](const auto &, const auto &j, bool) { return service.operation(j); });
    route("PUT", R"(/api/operations/([a-f0-9]+))", true,
          [&](const auto &q, const auto &j, bool) { return service.operation(j, q.matches[1]); });
    route("POST", R"(/api/operations/([a-f0-9]+)/approve)", true,
          [&](const auto &q, const auto &j, bool) {
            return service.approve_operation(q.matches[1], j);
          });
    route("POST", R"(/api/operations/([a-f0-9]+)/claim)", false,
          [&](const auto &q, const auto &, bool) { return service.claim_operation(q.matches[1]); });
    route("POST", R"(/api/operations/([a-f0-9]+)/result)", false,
          [&](const auto &q, const auto &j, bool) {
            return service.operation_result(q.matches[1], j);
          });
    route(
        "POST", "/api/model/run", false,
        [&](const auto &, const auto &j, bool) { return run_model(service, j); }, false);
    route("POST", "/api/goals", true, [&](const auto &, const auto &j, bool) {
      json v = {{"title", field(j, "title", 2000)},
                {"next_step", j.value("next_step", "")},
                {"priority", j.value("priority", 1)},
                {"state", j.value("state", "active")}};
      if (j.contains("id"))
        v["id"] = j["id"];
      return service.db.put("goals", v, j.value("expected_revision", 0));
    });
    route("POST", "/api/tables", false, [&](const auto &, const auto &j, bool) {
      field(j, "title", 2000);
      require(j.contains("columns") && j["columns"].is_array() && j.contains("rows") &&
                  j["rows"].is_array(),
              400, "Columns and rows required");
      auto v = j;
      v.erase("id");
      return service.db.put("tables", v, 0);
    });
    route("POST", "/api/backup", true, [&](const auto &, const auto &, bool) {
      auto p = data / "backups" / uid();
      fs::create_directories(p);
      service.sync_rule_files();
      service.db.backup(p / "assistant.db");
      if (fs::exists(data / "rules"))
        fs::copy(data / "rules", p / "rules", fs::copy_options::recursive);
      write(p / "manifest.json", json({{"created_at", now()},
                                       {"database", "assistant.db"},
                                       {"contains_credentials", false},
                                       {"note", "All indexed evidence is held in SQLite; raw "
                                                "external files remain external"}})
                                     .dump(2));
      return json{{"path", path_utf8(p)}};
    });
    http.Get("/", [&](const auto &, auto &r) {
      r.set_content(read(web / "index.html"), "text/html; charset=utf-8");
      r.set_header("Content-Security-Policy",
                   "default-src 'self'; connect-src 'self'; style-src 'self'; "
                   "script-src 'self'; frame-ancestors 'none'");
    });
    for (auto file : {"app.js", "providers.js", "connections.js", "conversations.js", "workspace-ui.js",
                      "lucide.min.js", "style.css"}) {
      std::string name = file;
      http.Get("/" + name, [&, name](const auto &, auto &r) {
        r.set_content(read(web / name), name.ends_with(".js") ? "text/javascript; charset=utf-8"
                                                              : "text/css; charset=utf-8");
      });
    }
    std::cout << "Personal assistant listening on " << host << ":" << port
              << ". Credentials are stored in the private data directory.\n"
              << std::flush;
    if (!http.listen(host, port))
      throw std::runtime_error("Could not listen on configured address");
    return 0;
  } catch (const std::exception &e) {
    std::cerr << e.what() << "\n";
    return 1;
  }
}
