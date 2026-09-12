// Headless GraphicsGL: textures are registered and drawn into the void.
// The real header is kept so every caller compiles unchanged.
#include "Graphics/GraphicsGL.h"

namespace jrc
{
    GraphicsGL::GraphicsGL()
        : locked(false), vbo(0), ibo(0), atlas(0), program(0),
          attribute_coord(0), attribute_color(0), uniform_texture(0),
          uniform_atlassize(0), uniform_screensize(0), uniform_yoffset(0),
          uniform_fontregion(0), rlid(0), wasted(0), ftlibrary(nullptr),
          fontymax(0) {}

    Error GraphicsGL::init()
    {
        return Error::NONE;
    }

    void GraphicsGL::reinit() {}

    void GraphicsGL::set_screensize(int16_t, int16_t) {}

    void GraphicsGL::clear() {}

    void GraphicsGL::addbitmap(const nl::bitmap&) {}

    void GraphicsGL::draw(const nl::bitmap&, const Rectangle<int16_t>&, const Color&, float) {}

    Text::Layout GraphicsGL::createlayout(const std::string&, Text::Font, Text::Alignment, int16_t, bool)
    {
        return Text::Layout();
    }

    void GraphicsGL::drawtext(const DrawArgument&, const std::string&, const Text::Layout&,
        Text::Font, Text::Color, Text::Background) {}

    void GraphicsGL::drawrectangle(int16_t, int16_t, int16_t, int16_t, float, float, float, float) {}

    void GraphicsGL::drawscreenfill(float, float, float, float) {}

    void GraphicsGL::lock()
    {
        locked = true;
    }

    void GraphicsGL::unlock()
    {
        locked = false;
    }

    void GraphicsGL::flush(float) {}

    void GraphicsGL::clearscene() {}
}
