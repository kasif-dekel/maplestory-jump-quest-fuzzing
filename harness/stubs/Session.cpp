// Headless Session: never connected. Outgoing packets are dropped exactly as
// the real Session drops them when the socket is closed.
#include "Net/Session.h"

namespace jrc
{
    Session::Session()
    {
        connected = false;
        length = 0;
        pos = 0;
    }

    Session::~Session() {}

    Error Session::init()
    {
        return Error::CONNECTION;
    }

    void Session::write(int8_t*, size_t) {}

    void Session::read() {}

    void Session::reconnect(const char*, const char*) {}

    bool Session::is_connected() const
    {
        return connected;
    }
}
