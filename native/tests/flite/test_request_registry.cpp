#include "request_registry.h"
#include <cassert>
#include <thread>
extern "C" void stts_cancel(stts_cancel_token* token) {
    if (token) __atomic_store_n(&token->requested, 1u, __ATOMIC_RELEASE);
}

int main() {
    RequestRegistry registry;
    const auto first = registry.create();
    assert(first > 0);
    auto request = registry.acquire(first);
    assert(request && request->begin());
    assert(!request->begin());
    registry.cancel(first);
    assert(__atomic_load_n(&request->token.requested, __ATOMIC_ACQUIRE));
    registry.close(first);
    assert(!registry.acquire(first));
    assert(!request->begin());
    request->finish();
    registry.close(first);

    const auto second = registry.create();
    assert(second > first);
    auto inFlight = registry.acquire(second);
    assert(inFlight && inFlight->begin());
    std::thread closer([&] { registry.close(second); });
    closer.join();
    assert(inFlight->isClosed());
    assert(__atomic_load_n(&inFlight->token.requested, __ATOMIC_ACQUIRE));
    inFlight->finish();
    assert(!registry.acquire(second));
    return 0;
}