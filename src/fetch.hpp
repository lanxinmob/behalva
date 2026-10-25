#pragma once
#include "model.hpp"
namespace pa {
inline std::string public_address(const std::string &host) {
  addrinfo hints{};
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;
  addrinfo *found = nullptr;
  require(getaddrinfo(host.c_str(), "443", &hints, &found) == 0, 502,
          "Website DNS lookup failed");
  std::unique_ptr<addrinfo, decltype(&freeaddrinfo)> info(found, freeaddrinfo);
  std::string chosen;
  for (auto a = found; a; a = a->ai_next) {
    char out[INET6_ADDRSTRLEN]{};
    bool public_ip = false;
    if (a->ai_family == AF_INET) {
      auto ip = ((sockaddr_in *)a->ai_addr)->sin_addr;
      auto n = ntohl(ip.s_addr);
      auto x = n >> 24, y = (n >> 16) & 255;
      public_ip =
          x != 0 && x != 10 && x != 127 && x < 224 && !(x == 169 && y == 254) &&
          !(x == 172 && y >= 16 && y <= 31) && !(x == 192 && y == 168) &&
          !(x == 100 && y >= 64 && y <= 127) &&
          !(x == 198 && (y == 18 || y == 19));
      inet_ntop(AF_INET, &ip, out, sizeof out);
    } else if (a->ai_family == AF_INET6) {
      auto ip = ((sockaddr_in6 *)a->ai_addr)->sin6_addr;
      auto p = (unsigned char *)&ip;
      public_ip = (p[0] & 0xe0) == 0x20;
      inet_ntop(AF_INET6, &ip, out, sizeof out);
    }
    require(public_ip, 403,
            "Only public Internet websites can receive automatic public-source "
            "authorization");
    if (chosen.empty())
      chosen = out;
  }
  require(!chosen.empty(), 502, "No public website address");
  return chosen;
}
inline std::string html_text(const std::string &html) {
  std::string out, lower = ascii_lower(html);
  bool tag = false;
  for (size_t i = 0; i < html.size(); ++i) {
    if (html[i] == '<') {
      if (lower.compare(i, 7, "<script") == 0 ||
          lower.compare(i, 6, "<style") == 0) {
        auto stop = lower.find(
            lower.compare(i, 7, "<script") == 0 ? "</script>" : "</style>", i);
        if (stop == std::string::npos)
          break;
        i = html.find('>', stop);
        continue;
      }
      tag = true;
      if (!out.empty() && out.back() != '\n')
        out += '\n';
    } else if (html[i] == '>')
      tag = false;
    else if (!tag)
      out += html[i];
  }
  for (auto [a, b] :
       std::vector<std::pair<std::string, std::string>>{{"&amp;", "&"},
                                                        {"&lt;", "<"},
                                                        {"&gt;", ">"},
                                                        {"&quot;", "\""},
                                                        {"&nbsp;", " "},
                                                        {"&#39;", "'"}}) {
    size_t p = 0;
    while ((p = out.find(a, p)) != std::string::npos) {
      out.replace(p, a.size(), b);
      p += b.size();
    }
  }
  return out;
}
inline json fetch_source(Service &s, const json &j) {
  auto url = field(j, "url", 8192);
  std::smatch m;
  require(std::regex_match(
              url, m, std::regex(R"(https://([A-Za-z0-9.-]+)(/[^\r\n]*)?)")),
          400, "Public fetch accepts HTTPS URLs on port 443");
  auto host = m[1].str(), path = m[2].matched ? m[2].str() : "/";
  auto addr = public_address(host);
  httplib::Client c("https://" + host);
  c.set_hostname_addr_map({{host, addr}});
  c.set_connection_timeout(10);
  c.set_read_timeout(20);
  c.set_follow_location(false);
  if (s.config.contains("ca_bundle"))
    c.set_ca_cert_path(s.config["ca_bundle"].get<std::string>().c_str());
  std::string raw;
  auto res = c.Get(path, {{"User-Agent", "PersonalAssistant/0.1"}},
                   [&](const char *p, size_t n) {
                     if (raw.size() + n > 4 * 1024 * 1024)
                       return false;
                     raw.append(p, n);
                     return true;
                   });
  require((bool)res, 502, "Website fetch failed");
  require(res->status == 200, 502,
          "Website returned HTTP " + std::to_string(res->status) +
              "; redirects and login need manual review");
  auto ct = res->get_header_value("Content-Type");
  require(ct.find("text/") != std::string::npos ||
              ct.find("json") != std::string::npos,
          415, "Use file import for non-text websites");
  auto text = ct.find("html") != std::string::npos ? html_text(raw) : raw;
  require(!text.empty(), 422, "Website returned no readable text");
  std::lock_guard lock(s.db.mutex);
  auto source =
      s.ingest({{"kind", "web"},
                {"uri", url},
                {"title", j.value("title", host)},
                {"body", text},
                {"coverage",
                 "Retrieved response only; embedded content not guaranteed"}});
  s.db.put("web_snapshots", {{"id", source["version_id"]},
                             {"url", url},
                             {"raw", raw},
                             {"content_type", ct},
                             {"retrieved_at", now()}});
  return source;
}
} // namespace pa
