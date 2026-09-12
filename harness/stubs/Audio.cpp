// Headless audio: every sound is silent and every init succeeds.
#include "Audio/Audio.h"

namespace jrc
{
    Sound::Sound(Name) : id(0) {}
    Sound::Sound(nl::node) : id(0) {}
    Sound::Sound() : id(0) {}

    void Sound::play() const {}

    Error Sound::init()
    {
        return Error::NONE;
    }

    void Sound::close() {}

    bool Sound::set_sfxvolume(uint8_t)
    {
        return true;
    }

    Music::Music(const std::string& p) : path(p) {}

    void Music::play() const {}

    Error Music::init()
    {
        return Error::NONE;
    }

    bool Music::set_bgmvolume(uint8_t)
    {
        return true;
    }
}
