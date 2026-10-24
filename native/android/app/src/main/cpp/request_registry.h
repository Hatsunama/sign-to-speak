#ifndef STTS_REQUEST_REGISTRY_H
#define STTS_REQUEST_REGISTRY_H

#include "flite/private_tts.h"
#include <atomic>
#include <cstdint>
#include <limits>
#include <memory>
#include <mutex>
#include <unordered_map>

class RequestRecord final {
public:
    stts_cancel_token token{};
    bool begin() noexcept {
        if (closed_.load(std::memory_order_acquire)) return false;
        bool idle = false;
        if (!active_.compare_exchange_strong(idle, true, std::memory_order_acq_rel)) return false;
        if (closed_.load(std::memory_order_acquire)) {
            active_.store(false, std::memory_order_release);
            return false;
        }
        return true;
    }
    void finish() noexcept { active_.store(false, std::memory_order_release); }
    void cancel() noexcept { stts_cancel(&token); }
    void close() noexcept {
        closed_.store(true, std::memory_order_release);
        stts_cancel(&token);
    }
    bool isClosed() const noexcept { return closed_.load(std::memory_order_acquire); }
    bool isCancelled() const noexcept {
        return __atomic_load_n(&token.requested, __ATOMIC_ACQUIRE) != 0;
    }
private:
    std::atomic<bool> active_{false};
    std::atomic<bool> closed_{false};
};

class RequestRegistry final {
public:
    int64_t create() {
        std::lock_guard<std::mutex> guard(mutex_);
        if (next_ == std::numeric_limits<int64_t>::max()) return 0;
        const int64_t id = ++next_;
        records_.emplace(id, std::make_shared<RequestRecord>());
        return id;
    }
    std::shared_ptr<RequestRecord> acquire(int64_t id) {
        std::lock_guard<std::mutex> guard(mutex_);
        const auto found = records_.find(id);
        return found == records_.end() ? nullptr : found->second;
    }
    void cancel(int64_t id) {
        auto request = acquire(id);
        if (request) request->cancel();
    }
    void close(int64_t id) {
        std::shared_ptr<RequestRecord> request;
        {
            std::lock_guard<std::mutex> guard(mutex_);
            const auto found = records_.find(id);
            if (found == records_.end()) return;
            request = found->second;
            records_.erase(found);
        }
        request->close();
    }
private:
    std::mutex mutex_;
    int64_t next_ = 0;
    std::unordered_map<int64_t, std::shared_ptr<RequestRecord>> records_;
};

#endif