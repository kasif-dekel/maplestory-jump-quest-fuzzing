// Headless Window: no GLFW, no events. The fade-out bookkeeping is kept so a
// caller relying on fadeout() still gets its callback.
#include "IO/Window.h"

namespace jrc
{
    Window::Window()
        : glwnd(nullptr), context(nullptr), fullscreen(false), f11_down(false),
          opacity(1.0f), opcstep(0.0f) {}

    Window::~Window() {}

    Error Window::init()
    {
        return Error::NONE;
    }

    Error Window::initwindow()
    {
        return Error::NONE;
    }

    bool Window::not_closed() const
    {
        return true;
    }

    void Window::update()
    {
        updateopc();
    }

    void Window::updateopc()
    {
        if (opcstep != 0.0f)
        {
            opacity += opcstep;
            if (opacity >= 1.0f)
            {
                opacity = 1.0f;
                opcstep = 0.0f;
            }
            else if (opacity <= 0.0f)
            {
                opacity = 0.0f;
                opcstep = -opcstep;
                fadeprocedure();
            }
        }
    }

    void Window::begin() const {}

    void Window::end() const {}

    void Window::fadeout(float step, std::function<void()> procedure)
    {
        opcstep = -step;
        fadeprocedure = procedure;
    }

    void Window::check_events() {}

    void Window::setclipboard(const std::string&) const {}

    std::string Window::getclipboard() const
    {
        return "";
    }
}
