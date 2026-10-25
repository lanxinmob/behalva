#pragma once
#include "json.hpp"
#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/sha.h>
#include <set>
#include <sstream>
#include <stdexcept>
#ifdef _WIN32
#include <winsock2.h>
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#endif
namespace pa {
using json = nlohmann::json;
namespace fs = std::filesystem;
inline fs::path path_from_utf8(const std::string &s) {
  return fs::path(
      std::u8string(reinterpret_cast<const char8_t *>(s.data()), s.size()));
}
inline std::string path_utf8(const fs::path &p) {
  auto s = p.generic_u8string();
  return std::string(reinterpret_cast<const char *>(s.data()), s.size());
}
struct Error : std::runtime_error {
  int status;
  json details;
  Error(int s, const std::string &m, json d = json::object())
      : std::runtime_error(m), status(s), details(d) {}
};
inline void require(bool ok, int status, const std::string &msg) {
  if (!ok)
    throw Error(status, msg);
}
inline int64_t now() {
  return std::chrono::duration_cast<std::chrono::seconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}
inline std::string hex(const unsigned char *p, size_t n) {
  std::ostringstream s;
  for (size_t i = 0; i < n; i++)
    s << std::hex << std::setw(2) << std::setfill('0') << (int)p[i];
  return s.str();
}
inline std::string hash(const std::string &s) {
  unsigned char b[SHA256_DIGEST_LENGTH];
  SHA256((const unsigned char *)s.data(), s.size(), b);
  return hex(b, sizeof b);
}
inline std::string uid() {
  unsigned char b[16];
  if (RAND_bytes(b, sizeof b) != 1)
    throw std::runtime_error("random generator failed");
  return hex(b, sizeof b);
}
inline std::string read(const fs::path &p) {
  std::ifstream f(p, std::ios::binary);
  if (!f)
    throw Error(404, "File not found");
  return {std::istreambuf_iterator<char>(f), {}};
}
inline std::string prefix(const std::string &s, size_t n) {
  if (s.size() <= n)
    return s;
  while (n > 0 && ((unsigned char)s[n] & 0xc0) == 0x80)
    --n;
  return s.substr(0, n);
}
inline std::string ascii_lower(std::string s) {
  for (char &c : s)
    if (c >= 'A' && c <= 'Z')
      c += 32;
  return s;
}
inline std::string unbase64(const std::string &s) {
  require(!s.empty() && s.size() % 4 == 0, 400, "Invalid base64");
  std::string out(s.size() * 3 / 4, '\0');
  int n = EVP_DecodeBlock((unsigned char *)out.data(),
                          (const unsigned char *)s.data(), (int)s.size());
  require(n >= 0, 400, "Invalid base64");
  if (s.ends_with("=="))
    n -= 2;
  else if (s.ends_with("="))
    --n;
  out.resize(n);
  return out;
}
struct InstanceLock {
#ifdef _WIN32
  HANDLE h;
  explicit InstanceLock(const fs::path &p) {
    h = CreateFileW(p.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                    OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    require(h != INVALID_HANDLE_VALUE, 409,
            "Another service owns this data directory");
  }
  ~InstanceLock() { CloseHandle(h); }
#else
  int fd;
  explicit InstanceLock(const fs::path &p) {
    fd = open(p.c_str(), O_CREAT | O_RDWR, 0600);
    if (fd < 0 || flock(fd, LOCK_EX | LOCK_NB) != 0) {
      if (fd >= 0)
        close(fd);
      throw Error(409, "Another service owns this data directory");
    }
  }
  ~InstanceLock() {
    flock(fd, LOCK_UN);
    close(fd);
  }
#endif
};
inline void write(const fs::path &p, const std::string &s) {
  fs::create_directories(p.parent_path());
  std::ofstream f(p, std::ios::binary | std::ios::trunc);
  if (!f || !f.write(s.data(), s.size()))
    throw std::runtime_error("File write failed");
}
inline bool equal_secret(const std::string &a, const std::string &b) {
  return a.size() == b.size() &&
         CRYPTO_memcmp(a.data(), b.data(), a.size()) == 0;
}
inline std::string field(const json &j, const char *k, size_t max = 100000) {
  require(j.contains(k) && j[k].is_string(), 400,
          std::string("Missing string: ") + k);
  auto s = j[k].get<std::string>();
  require(!s.empty() && s.size() <= max, 400,
          std::string("Invalid length: ") + k);
  return s;
}
inline bool within(const fs::path &child, const fs::path &parent) {
  auto c = fs::weakly_canonical(child);
  auto p = fs::weakly_canonical(parent);
  auto ci = c.begin();
  for (auto pi = p.begin(); pi != p.end(); ++pi, ++ci) {
    if (ci == c.end())
      return false;
#ifdef _WIN32
    if (CompareStringOrdinal(ci->c_str(), -1, pi->c_str(), -1, TRUE) !=
        CSTR_EQUAL)
      return false;
#else
    if (*ci != *pi)
      return false;
#endif
  }
  return true;
}
} // namespace pa
