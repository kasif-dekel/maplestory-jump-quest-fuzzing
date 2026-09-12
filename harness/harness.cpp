// Fuzzing harness: runs the real client Stage/Player/Physics code on one map
// from a deterministic snapshot and drives it with fuzzer-generated input.
//
//   zakum_harness [options] <input-file|->
//     --assets DIR     directory holding the .nx files (default: .)
//     --map ID         map id to spawn on (default: 280020000)
//     --portal ID      spawn portal id (default: 0)
//     --max-ticks N    physics tick budget per run (default: 20000)
//     --trace          print "T tick x y state onground" per tick to stdout
//     --dump-map       print footholds/portals/spawn as JSON and exit
//     --hp N           hit points (default 100); --damage N damage per hit (default 1)
//     --lava-hurts     lava areas cost --damage like any trap instead of killing
//                      outright (default: lethal; with HP and knock-back, wading
//                      along the bottom otherwise reaches the exit platform)
//     --death-y Y      falling below this y (larger value) ends the run
//                      (off by default: the lava is covered by damage traps)
//     --route FILE     route table from `best.py --export-route` (platform -> hops
//                      to the goal): IJON slot = hops, value = closeness to the
//                      point where the next hop starts; floors and towers that
//                      do not lead to the goal earn nothing
//     --speed          speedrun mode (with --route): the value of a hop level is
//                      how EARLY the run first reaches it, so the fuzzer shortens
//                      a solution instead of extending it; seed it with solutions.
//                      Solving is then a normal exit (IJON slot 500 = earliest
//                      solve), not an abort, so solutions can be seeds
//     --floor-y Y      no IJON credit for positions below this y (larger value);
//                      default: top edge of the map's lava trap areas, so
//                      wading through the lava is never rewarded as progress
//     --phases N       IJON slots per height band, one per phase of the falling
//                      rocks' cycle (default 8; 1 disables). Inputs that reach
//                      the same x at a different rock timing are kept apart.
//     --cycle-ticks N  override the measured rock cycle length
//     --goal G         what solves the map: "" = any warp portal to another map
//                      (default), "portal:NAME", "npc:ID" (stand next to it) or
//                      "xy:X,Y" (stand on the ground within 40 x 30 px of the point)
//     --hidden-portals reset|ignore   invisible target-less portals (default reset)
//     --no-mobs        do not spawn the map's mobs (Firebombs on the Zakum maps)
//     --spawn X,Y      override the spawn position (testing hazards)
//     --prefix FILE    checkpoint: replay these input bytes from the spawn
//                      before the forkserver snapshot, then fuzz what follows.
//                      Ticks keep counting, so <prefix bytes> + <fuzz input>
//                      is an ordinary input that replays from the spawn.
//     --via-replay     feed input through Offline::set_replay/tick like the
//                      browser does instead of the harness loop (equivalence test)
//     --keep-going     do not exit on reset portals / death / hits (for tracing)
//
// Exit protocol (what AFL sees):
//   reset portal hit  -> _exit(0)   dead end (a warp with an in-map target continues)
//   fell below death-y-> _exit(0)   dead end
//   HP reaches 0      -> _exit(0)   dead end (mob touches and trap hits each
//                                    cost --damage HP; two seconds of
//                                    invincibility follow every hit)
//   exit portal hit   -> abort()    solution, filed under crashes/
//   input exhausted   -> 96 more ticks without keys (--tail-bytes), then _exit(0)
//   setup failure     -> exit(2)   (also: death or exhausted budget inside --prefix)
#include "Character/Char.h"
#include "Configuration.h"
#include "Gameplay/Combat/DamageNumber.h"
#include "Gameplay/MapleMap/MapPortals.h"
#include "Gameplay/MapleMap/MapTilesObjs.h"
#include "Gameplay/Stage.h"
#include "Offline/Offline.h"
#include "Util/NxFiles.h"

#ifdef IJON_ENABLED
#include "afl-rt.h"
#endif

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <unordered_map>
#include <unistd.h>
#include <climits>

namespace
{
    using namespace jrc;

    constexpr size_t MAX_INPUT_BYTES = 64 * 1024;
    // Height band per IJON slot. The paper used one slot per tile row.
    constexpr int32_t SLOT_HEIGHT = 16;

    struct Options
    {
        std::string assets = ".";
        int32_t map = 280020000;
        int32_t portal = 0;
        int32_t max_ticks = 20000;
        int32_t tail_bytes = 24;    // key-free ticks after the input: 24 bytes = 96 ticks, 1.5 jumps
        int32_t death_y = INT32_MAX;
        int32_t hp = 100;
        int32_t damage = 1;
        bool lava_lethal = true;
        int32_t phases = 8;
        int32_t cycle_ticks = 0;
        int32_t floor_y = INT32_MAX;
        std::string route;          // --route FILE: hops-to-goal table (best.py --export-route)
        bool speed = false;         // --speed: reward reaching each hop level EARLIER (speedrun mode)
        bool trace = false;
        bool dump_map = false;
        bool keep_going = false;
        bool mobs = true;
        bool via_replay = false;
        bool spawn_override = false;
        int32_t spawn_x = 0;
        int32_t spawn_y = 0;
        std::string input;
        std::string prefix;
        std::string goal;
        std::string hidden_portals = "reset";
    };

    Options g_opts;
    // Trace/JSON output. The client chatters on std::cout, so stdout is
    // redirected to stderr at startup and machine-readable output goes here.
    FILE* g_out = stdout;
    int32_t g_tick = 0;
    int16_t g_spawn_y = 0;
    int32_t g_reset_count = 0;
    // Goal-distance progress metric (see feedback()).
    Point<int16_t> g_goal_pos;
    bool g_have_goal = false;
    double g_maxdist = 1.0;    // progress baseline: farthest map corner from the goal (+64)
    double g_spawndist = 0.0;  // for the log line
    // --route table: foothold id -> (hops to goal, x where the next hop starts)
    struct RouteEntry { int32_t hops; int32_t tx; };
    std::unordered_map<int32_t, RouteEntry> g_route_fh;
    struct RouteLadder { int32_t x, top, bot, hops; };
    std::vector<RouteLadder> g_route_ladders;
    bool g_have_route = false;
    int32_t g_route_max_hops = 0;

    bool load_route(const std::string& path)
    {
        FILE* f = std::fopen(path.c_str(), "r");
        if (!f) return false;
        char line[256];
        while (std::fgets(line, sizeof line, f))
        {
            int32_t a, b, c, d;
            if (std::sscanf(line, "F %d %d %d", &a, &b, &c) == 3)
            {
                g_route_fh[a] = RouteEntry{b, c};
                g_route_max_hops = std::max(g_route_max_hops, b);
            }
            else if (std::sscanf(line, "L %d %d %d %d", &a, &b, &c, &d) == 4)
            {
                g_route_ladders.push_back(RouteLadder{a, b, c, d});
                g_route_max_hops = std::max(g_route_max_hops, d);
            }
        }
        std::fclose(f);
        return !g_route_fh.empty();
    }

    bool g_bands_by_y = true;    // band axis perpendicular to the main direction of travel
    int32_t g_band_size = 16;
    int32_t g_band_origin = 0;
    // True while the --prefix bytes are being replayed: dying or running out of
    // budget there is a setup error, not a fuzzing result.
    bool g_in_prefix = false;

    void prefix_failed(const char* what)
    {
        std::fprintf(stderr, "PREFIX FAILED: %s at tick %d\n", what, g_tick);
        std::fflush(g_out);
        std::exit(2);
    }

    void usage(const char* argv0)
    {
        std::fprintf(stderr, "usage: %s [--assets DIR] [--map ID] [--portal ID] [--max-ticks N] [--tail-bytes N] [--hp N] [--damage N] [--lava-hurts] [--death-y Y] [--phases N] [--cycle-ticks N] [--floor-y Y] [--route FILE] [--speed] [--goal G] [--hidden-portals reset|reset-all|ignore] [--no-mobs] [--prefix FILE] [--trace] [--dump-map] [--keep-going] <input|->\n", argv0);
        std::exit(2);
    }

    Options parse_args(int argc, char** argv)
    {
        Options opts;
        for (int i = 1; i < argc; ++i)
        {
            std::string arg = argv[i];
            auto need_value = [&](const char* name) -> const char*
            {
                if (i + 1 >= argc)
                {
                    std::fprintf(stderr, "%s needs a value\n", name);
                    usage(argv[0]);
                }
                return argv[++i];
            };
            if (arg == "--assets") opts.assets = need_value("--assets");
            else if (arg == "--map") opts.map = std::atoi(need_value("--map"));
            else if (arg == "--portal") opts.portal = std::atoi(need_value("--portal"));
            else if (arg == "--max-ticks") opts.max_ticks = std::atoi(need_value("--max-ticks"));
            else if (arg == "--tail-bytes") opts.tail_bytes = std::atoi(need_value("--tail-bytes"));
            else if (arg == "--death-y") opts.death_y = std::atoi(need_value("--death-y"));
            else if (arg == "--hp") opts.hp = std::atoi(need_value("--hp"));
            else if (arg == "--damage") opts.damage = std::atoi(need_value("--damage"));
            else if (arg == "--lava-hurts") opts.lava_lethal = false;
            else if (arg == "--phases") opts.phases = std::atoi(need_value("--phases"));
            else if (arg == "--floor-y") opts.floor_y = std::atoi(need_value("--floor-y"));
            else if (arg == "--route") opts.route = need_value("--route");
            else if (arg == "--speed") opts.speed = true;
            else if (arg == "--cycle-ticks") opts.cycle_ticks = std::atoi(need_value("--cycle-ticks"));
            else if (arg == "--trace") opts.trace = true;
            else if (arg == "--dump-map") opts.dump_map = true;
            else if (arg == "--keep-going") opts.keep_going = true;
            else if (arg == "--no-mobs") opts.mobs = false;
            else if (arg == "--via-replay") opts.via_replay = true;
            else if (arg == "--prefix") opts.prefix = need_value("--prefix");
            else if (arg == "--goal") opts.goal = need_value("--goal");
            else if (arg == "--hidden-portals") opts.hidden_portals = need_value("--hidden-portals");
            else if (arg == "--spawn")
            {
                const char* v = need_value("--spawn");
                if (std::sscanf(v, "%d,%d", &opts.spawn_x, &opts.spawn_y) != 2)
                {
                    std::fprintf(stderr, "--spawn needs X,Y\n");
                    usage(argv[0]);
                }
                opts.spawn_override = true;
            }
            else if (arg == "--help" || arg == "-h") usage(argv[0]);
            else if (!arg.empty() && arg[0] == '-' && arg != "-")
            {
                std::fprintf(stderr, "unknown option %s\n", arg.c_str());
                usage(argv[0]);
            }
            else opts.input = arg;
        }
        if (!opts.dump_map && opts.input.empty())
        {
            usage(argv[0]);
        }
        return opts;
    }

    std::vector<uint8_t> read_input(const std::string& path)
    {
        std::vector<uint8_t> data(MAX_INPUT_BYTES);
        if (path.empty())
        {
            return {};
        }
        FILE* f = path == "-" ? stdin : std::fopen(path.c_str(), "rb");
        if (!f)
        {
            std::fprintf(stderr, "cannot open %s\n", path.c_str());
            std::exit(2);
        }
        size_t n = std::fread(data.data(), 1, data.size(), f);
        if (f != stdin)
        {
            std::fclose(f);
        }
        data.resize(n);
        return data;
    }

    void dump_map()
    {
        const Stage& stage = Stage::get();
        const Footholdtree& fht = stage.get_physics().get_fht();
        Player& player = Stage::get().get_player();
        Point<int16_t> pos = player.get_position();

        std::fprintf(g_out, "{\n  \"map\": %d,\n  \"spawn\": [%d, %d],\n  \"rock_cycle_ticks\": %d,\n  \"lava_top_y\": %d,\n  \"goal\": [%d, %d],\n  \"goal_found\": %s,\n",
            g_opts.map, pos.x(), pos.y(), g_opts.cycle_ticks, g_opts.floor_y == INT32_MAX ? 0 : g_opts.floor_y,
            g_goal_pos.x(), g_goal_pos.y(), g_have_goal ? "true" : "false");
        std::fprintf(g_out, "  \"walls\": [%d, %d],\n  \"borders\": [%d, %d],\n",
            fht.get_walls().first(), fht.get_walls().second(),
            fht.get_borders().first(), fht.get_borders().second());

        std::fprintf(g_out, "  \"footholds\": [\n");
        bool first = true;
        for (const auto& iter : fht.get_footholds())
        {
            const Foothold& fh = iter.second;
            std::fprintf(g_out, "%s    {\"id\": %u, \"layer\": %u, \"x1\": %d, \"y1\": %d, \"x2\": %d, \"y2\": %d, \"prev\": %u, \"next\": %u}",
                first ? "" : ",\n", fh.id(), fh.layer(), fh.x1(), fh.y1(), fh.x2(), fh.y2(), fh.prev(), fh.next());
            first = false;
        }
        std::fprintf(g_out, "\n  ],\n  \"portals\": [\n");
        first = true;
        for (const auto& iter : stage.get_portals().get_portals())
        {
            const Portal& portal = iter.second;
            Portal::WarpInfo info = portal.getwarpinfo();
            std::fprintf(g_out, "%s    {\"id\": %u, \"type\": %d, \"name\": \"%s\", \"x\": %d, \"y\": %d, \"tomap\": %d, \"toname\": \"%s\", \"intramap\": %s, \"valid\": %s}",
                first ? "" : ",\n", iter.first, static_cast<int>(portal.get_type()), portal.get_name().c_str(),
                portal.get_position().x(), portal.get_position().y(), info.mapid, info.toname.c_str(),
                info.intramap ? "true" : "false", info.valid ? "true" : "false");
            first = false;
        }
        std::fprintf(g_out, "\n  ],\n  \"traps\": [\n");
        first = true;
        for (const Obj* trap : stage.get_tilesobjs().get_traps())
        {
            Rectangle<int16_t> box = trap->get_hitbox();
            std::fprintf(g_out, "%s    {\"name\": \"%s\", \"x\": %d, \"y\": %d, \"damage\": %d, \"box\": [%d, %d, %d, %d], \"move\": [%d, %d, %d, %d]}",
                first ? "" : ",\n", trap->get_name().c_str(), trap->get_anchor().x(), trap->get_anchor().y(),
                trap->get_damage(), box.l(), box.t(), box.r(), box.b(),
                trap->get_move_type(), trap->get_move_w(), trap->get_move_h(), trap->get_move_p());
            first = false;
        }
        std::fprintf(g_out, "\n  ],\n  \"ladders\": [\n");
        first = true;
        for (const Ladder& ladder : stage.get_map_info().get_ladders())
        {
            std::fprintf(g_out, "%s    {\"x\": %d, \"y1\": %d, \"y2\": %d, \"ladder\": %s}",
                first ? "" : ",\n", ladder.get_x(), ladder.get_y1(), ladder.get_y2(), ladder.is_ladder() ? "true" : "false");
            first = false;
        }
        std::fprintf(g_out, "\n  ],\n  \"npcs\": [\n");
        first = true;
        for (const Offline::MobInfo& npc : Offline::map_npcs())
        {
            std::fprintf(g_out, "%s    {\"id\": %d, \"x\": %d, \"y\": %d}", first ? "" : ",\n", npc.id, npc.x, npc.y);
            first = false;
        }
        std::fprintf(g_out, "\n  ],\n  \"mobs\": [\n");
        first = true;
        for (const Offline::MobInfo& mob : Offline::spawned_mobs())
        {
            std::fprintf(g_out, "%s    {\"id\": %d, \"x\": %d, \"y\": %d}", first ? "" : ",\n", mob.id, mob.x, mob.y);
            first = false;
        }
        std::fprintf(g_out, "\n  ]\n}\n");
    }

    void on_reset(const Portal::WarpInfo& info)
    {
        if (info.intramap && info.valid)
        {
            // A portal with a target inside the map (the Kerning subway's
            // hidden portals lift the player to the upper platforms): the
            // warp is part of the map, the run goes on from the target.
            if (g_opts.trace)
            {
                std::fprintf(g_out, "P %d %s\n", g_tick, info.name.c_str());
            }
            return;
        }
        if (g_in_prefix) prefix_failed("reset portal");
        g_reset_count++;
        if (g_opts.trace)
        {
            std::fprintf(g_out, "R %d %s\n", g_tick, info.name.c_str());
        }
        if (!g_opts.keep_going)
        {
            std::fprintf(stderr, "RESET tick=%d portal=%s\n", g_tick, info.name.c_str());
            std::fflush(g_out);
            _exit(0);
        }
    }

    // Trace field: 1 standing on a foothold, 2 climbing a rope or ladder,
    // 0 airborne. Both 1 and 2 are places a checkpoint may cut at.
    int ground_state(Player& player)
    {
        return player.get_phobj().onground ? 1 : (player.is_climbing() ? 2 : 0);
    }

    void on_hit(int32_t mob_oid)
    {
        if (g_in_prefix) prefix_failed("killed by a mob");
        Point<int16_t> pos = Stage::get().get_player().get_position();
        if (g_opts.trace)
        {
            std::fprintf(g_out, "H %d %d %d %d\n", g_tick, pos.x(), pos.y(), mob_oid);
        }
        if (!g_opts.keep_going)
        {
            std::fprintf(stderr, "HIT tick=%d x=%d y=%d mob=%d\n", g_tick, pos.x(), pos.y(), mob_oid);
            std::fflush(g_out);
            _exit(0);
        }
    }

    void on_damage(int32_t damage, int32_t hp_left, const std::string& why)
    {
        if (g_opts.trace)
        {
            Point<int16_t> pos = Stage::get().get_player().get_position();
            std::fprintf(g_out, "G %d %d %d %d %d %s\n", g_tick, pos.x(), pos.y(), damage, hp_left, why.c_str());
        }
    }

    void on_trap(int32_t damage, const std::string& name)
    {
        if (g_in_prefix) prefix_failed("killed by a trap");
        Point<int16_t> pos = Stage::get().get_player().get_position();
        if (g_opts.trace)
        {
            std::fprintf(g_out, "X %d %d %d %d %s\n", g_tick, pos.x(), pos.y(), damage, name.c_str());
        }
        if (!g_opts.keep_going)
        {
            std::fprintf(stderr, "TRAP tick=%d x=%d y=%d damage=%d trap=%s\n", g_tick, pos.x(), pos.y(), damage, name.c_str());
            std::fflush(g_out);
            _exit(0);
        }
    }

    void on_exit(const Portal::WarpInfo& info)
    {
        if (g_in_prefix) prefix_failed("reached the exit portal (nothing left to fuzz)");
        if (g_opts.trace)
        {
            std::fprintf(g_out, "W %d %s %d\n", g_tick, info.name.c_str(), info.mapid);
        }
        std::fprintf(stderr, "SOLVED tick=%d portal=%s tomap=%d\n", g_tick, info.name.c_str(), info.mapid);
        std::fflush(g_out);
        std::fflush(stderr);
        if (g_opts.speed)
        {
            // Speedrun mode: solving is not a crash but the best possible
            // outcome; IJON slot 500 keeps the input that solves earliest and
            // the solutions stay in the queue as seeds for faster ones.
#ifdef IJON_ENABLED
            ijon_max(500u, static_cast<ijon_u64_t>((1 << 20) - g_tick));
#endif
            _exit(0);
        }
        // AFL records the solving input as a crash; that is intentional.
        std::abort();
    }

    // Length of the falling rocks' animation cycle in ticks, measured by
    // stepping a copy of the first stone trap until its hit box reappears.
    // 0 when the map has no such trap.
    int32_t measure_rock_cycle()
    {
        for (const Obj* trap : Stage::get().get_tilesobjs().get_traps())
        {
            if (trap->get_name().find("trap/stone") == std::string::npos)
            {
                continue;
            }
            Obj probe = *trap;
            auto armed = [&probe]() {
                Rectangle<int16_t> box = probe.get_hitbox();
                return box.width() != 0 || box.height() != 0;
            };
            // Find the first rising edge, then the next one.
            int32_t tick = 0;
            bool prev = armed();
            int32_t first = -1;
            while (tick < 20000)
            {
                probe.update();
                tick++;
                bool now = armed();
                if (now && !prev)
                {
                    if (first < 0)
                    {
                        first = tick;
                    }
                    else
                    {
                        return tick - first;
                    }
                }
                prev = now;
            }
            return 0;
        }
        return 0;
    }

    // Highest point (smallest y) of the lava trap areas: anything below it is
    // lava, and progress made there is not the jump quest.
    int32_t measure_lava_top()
    {
        int32_t top = INT32_MAX;
        for (const Obj* trap : Stage::get().get_tilesobjs().get_traps())
        {
            if (trap->get_name().find("trap/area") == std::string::npos)
            {
                continue;
            }
            Rectangle<int16_t> box = trap->get_hitbox();
            if (box.width() != 0 || box.height() != 0)
            {
                top = std::min<int32_t>(top, box.t());
            }
        }
        return top;
    }

    // Where the run should end: the --goal portal or NPC, or by default the
    // warp portal farthest from the spawn. Returns false if nothing is found.
    bool resolve_goal(Point<int16_t> spawn)
    {
        const std::string& goal = g_opts.goal;
        if (goal.compare(0, 3, "xy:") == 0)
        {
            // "xy:X,Y": stand on the ground within 40 x 30 px of a map point
            // (a reactor, a chest, the top of a tower without an exit portal).
            int32_t x = 0, y = 0;
            if (std::sscanf(goal.c_str() + 3, "%d,%d", &x, &y) != 2)
            {
                return false;
            }
            g_goal_pos = Point<int16_t>(static_cast<int16_t>(x), static_cast<int16_t>(y));
            return true;
        }
        if (goal.compare(0, 4, "npc:") == 0)
        {
            int32_t id = std::atoi(goal.c_str() + 4);
            for (const Offline::MobInfo& npc : Offline::map_npcs())
            {
                if (npc.id == id)
                {
                    g_goal_pos = Point<int16_t>(npc.x, npc.y);
                    return true;
                }
            }
            return false;
        }
        std::string name = goal.compare(0, 7, "portal:") == 0 ? goal.substr(7) : goal;
        const Portal* best = nullptr;
        double bestd = -1;
        for (const auto& iter : Stage::get().get_portals().get_portals())
        {
            const Portal& portal = iter.second;
            Portal::WarpInfo info = portal.getwarpinfo();
            if (!name.empty())
            {
                if (portal.get_name() == name)
                {
                    g_goal_pos = portal.get_position();
                    return true;
                }
                continue;
            }
            if (!info.valid || info.intramap)
            {
                continue;
            }
            double dx = portal.get_position().x() - spawn.x();
            double dy = portal.get_position().y() - spawn.y();
            double d = dx * dx + dy * dy;
            if (d > bestd)
            {
                bestd = d;
                best = &portal;
            }
        }
        if (best)
        {
            g_goal_pos = best->get_position();
            return true;
        }
        return false;
    }

    // Set up the progress metric: value = how much closer to the goal than the
    // spawn is (in px), slots = bands perpendicular to the spawn->goal axis so
    // different routes are explored independently, times the hazard phase.
    void setup_metric(Point<int16_t> spawn, const Footholdtree& fht)
    {
        g_have_goal = resolve_goal(spawn);
        if (!g_have_goal)
        {
            // Fallback: the old horizontal metric (progress = x).
            g_goal_pos = Point<int16_t>(fht.get_walls().second(), spawn.y());
        }
        double dx = g_goal_pos.x() - spawn.x();
        double dy = g_goal_pos.y() - spawn.y();
        g_spawndist = std::sqrt(dx * dx + dy * dy);
        // Progress is measured from the map corner farthest from the goal, not
        // from the spawn: many jump quests first lead away from the exit (the
        // Forest of Patience staircases start by going right), and a baseline
        // at the spawn would give those first platforms no credit at all.
        g_maxdist = g_spawndist;
        for (int32_t cx : {fht.get_walls().first(), fht.get_walls().second()})
        {
            for (int32_t cy : {fht.get_borders().first(), fht.get_borders().second()})
            {
                double ex = g_goal_pos.x() - cx;
                double ey = g_goal_pos.y() - cy;
                g_maxdist = std::max(g_maxdist, std::sqrt(ex * ex + ey * ey));
            }
        }
        g_maxdist += 64.0;
        g_bands_by_y = std::abs(dx) >= std::abs(dy);
        int32_t span = g_bands_by_y
            ? fht.get_borders().second() - fht.get_borders().first()
            : fht.get_walls().second() - fht.get_walls().first();
        g_band_origin = g_bands_by_y ? fht.get_borders().first() : fht.get_walls().first();
        int32_t phases = g_opts.phases > 0 ? g_opts.phases : 1;
        // IJON has MAXMAP_SIZE (512) slots; keep bands * phases inside it.
        g_band_size = std::max(SLOT_HEIGHT, (span * phases + 479) / 480);
    }

    // The paper's Mario annotation, generalised: one slot per band across the
    // direction of travel (height bands on a horizontal map, columns on a
    // vertical one), maximise progress toward the goal within each band so a
    // dead end on one route does not starve the others. Each band is further
    // split by the phase of the falling rocks' cycle: otherwise every frontier
    // input inherits the same arrival time from its prefix and dies to the same
    // rock, and the fuzzer has no incentive to arrive a little earlier or later.
    void feedback(Point<int16_t> pos, const Footholdtree& fht, bool grounded, uint16_t fhid, bool climbing)
    {
        (void)fht;
        if (pos.y() > g_opts.floor_y)
        {
            // In the lava: survivable for a while with HP, but not progress.
            return;
        }
        if (!grounded)
        {
            // Only positions on a foothold or a rope count. The apex of a failed
            // jump rises above the next platform, so mid-air credit lets a miss
            // earn as much as a landing, and landing then earns nothing new.
            return;
        }
        if (g_have_route)
        {
            // Route mode: one IJON slot per hop count, the value grows towards
            // the point of the platform where the next hop starts (or up a
            // rope). A platform the route model cannot connect to the goal
            // earns nothing, so floors and dead-end towers that are close to
            // the goal in pixels do not swallow the fuzzer's attention.
            int32_t hops = -1;
            int64_t value = 0;
            if (climbing)
            {
                for (const RouteLadder& l : g_route_ladders)
                {
                    if (std::abs(pos.x() - l.x) <= 12 && pos.y() >= l.top - 10 && pos.y() <= l.bot + 10)
                    {
                        hops = l.hops;
                        value = 4096 + (l.bot - pos.y());
                        break;
                    }
                }
            }
            else
            {
                auto it = g_route_fh.find(fhid);
                if (it != g_route_fh.end())
                {
                    hops = it->second.hops;
                    value = 4096 - std::min<int32_t>(4095, std::abs(pos.x() - it->second.tx));
                }
            }
            if (hops < 0)
            {
                return;
            }
            if (g_opts.speed)
            {
                // Speedrun: the first tick this run stands on a platform of
                // this hop level is the value (earlier = larger); later
                // ticks on the same level add nothing.
                // (each fuzz run is a fork of the snapshot taken before any
                // credited tick, so this array starts empty in every run)
                static bool reached[512];
                int32_t slot = std::min(hops, 511);
                if (reached[slot])
                {
                    return;
                }
                reached[slot] = true;
                value = (1 << 20) - g_tick;
            }
            if (g_opts.trace)
            {
                // "I tick hops value": the route feedback this tick would give
                // IJON, printed when it improves on this run's best for the slot
                static std::unordered_map<int32_t, int64_t> best;
                auto b = best.find(hops);
                if (b == best.end() || value > b->second)
                {
                    best[hops] = value;
                    std::fprintf(g_out, "I %d %d %lld\n", g_tick, hops, static_cast<long long>(value));
                }
            }
#ifdef IJON_ENABLED
            ijon_max(static_cast<ijon_u32_t>(std::min(hops, 511)), static_cast<ijon_u64_t>(value));
#else
            (void)value;
#endif
            return;
        }
        int32_t along = g_bands_by_y ? pos.y() : pos.x();
        int32_t band = (along - g_band_origin) / g_band_size;
        if (band < 0) band = 0;
        int32_t phase = 0;
        if (g_opts.phases > 1 && g_opts.cycle_ticks > 0)
        {
            phase = static_cast<int32_t>((static_cast<int64_t>(g_tick % g_opts.cycle_ticks) * g_opts.phases) / g_opts.cycle_ticks);
        }
        int32_t slot = band * (g_opts.phases > 0 ? g_opts.phases : 1) + phase;
        double dx = g_goal_pos.x() - pos.x();
        double dy = g_goal_pos.y() - pos.y();
        double dist = std::sqrt(dx * dx + dy * dy);
        int64_t progress = static_cast<int64_t>(g_maxdist - dist);
        if (progress < 0) progress = 0;
#ifdef IJON_ENABLED
        ijon_max(static_cast<ijon_u32_t>(slot), static_cast<ijon_u64_t>(progress));
#else
        (void)slot;
        (void)progress;
#endif
    }

    void on_death(Point<int16_t> pos)
    {
        if (g_in_prefix) prefix_failed("fell below death-y");
        if (g_opts.trace)
        {
            std::fprintf(g_out, "D %d %d %d\n", g_tick, pos.x(), pos.y());
        }
        if (!g_opts.keep_going)
        {
            std::fprintf(stderr, "FELL tick=%d x=%d y=%d\n", g_tick, pos.x(), pos.y());
            std::fflush(g_out);
            _exit(0);
        }
    }
}

int main(int argc, char** argv)
{
    g_opts = parse_args(argc, argv);

    {
        int out_fd = dup(STDOUT_FILENO);
        if (out_fd >= 0 && dup2(STDERR_FILENO, STDOUT_FILENO) >= 0)
        {
            g_out = fdopen(out_fd, "w");
        }
        if (!g_out)
        {
            g_out = stderr;
        }
    }

    // Input and prefix paths must survive the chdir into the assets directory.
    for (std::string* path : { &g_opts.input, &g_opts.prefix })
    {
        if (!path->empty() && *path != "-")
        {
            char resolved[PATH_MAX];
            if (realpath(path->c_str(), resolved))
            {
                *path = resolved;
            }
        }
    }

    if (chdir(g_opts.assets.c_str()) != 0)
    {
        std::fprintf(stderr, "cannot chdir to assets dir %s\n", g_opts.assets.c_str());
        return 2;
    }

    Setting<OfflineMapId>::get().save(std::to_string(g_opts.map));
    Setting<OfflinePortal>::get().save(std::to_string(g_opts.portal));
    Setting<OfflineReplay>::get().save("");
    Setting<OfflineHp>::get().save(std::to_string(g_opts.hp));
    Setting<OfflineDamagePerHit>::get().save(std::to_string(g_opts.damage));
    Setting<OfflineLavaLethal>::get().save(g_opts.lava_lethal ? "1" : "0");
    Setting<OfflineGoal>::get().save(g_opts.goal);
    Setting<OfflineHiddenPortals>::get().save(g_opts.hidden_portals);

    if (Error error = NxFiles::init())
    {
        std::fprintf(stderr, "nx init failed: %s %s\n", error.get_message(), error.get_args());
        return 2;
    }

    Char::init();
    DamageNumber::init();
    MapPortals::init();
    Stage::get().init();
    Offline::set_hazards(g_opts.mobs);
    Offline::spawn(false);
    if (g_opts.spawn_override)
    {
        Stage::get().get_player().respawn(
            Point<int16_t>(static_cast<int16_t>(g_opts.spawn_x), static_cast<int16_t>(g_opts.spawn_y)), false);
    }

    const Footholdtree& fht = Stage::get().get_physics().get_fht();
    if (fht.get_footholds().empty())
    {
        std::fprintf(stderr, "map %d has no footholds (missing from Map.nx?)\n", g_opts.map);
        return 2;
    }

    Player& player = Stage::get().get_player();
    g_spawn_y = player.get_position().y();

    if (g_opts.cycle_ticks == 0)
    {
        g_opts.cycle_ticks = measure_rock_cycle();
    }
    if (g_opts.cycle_ticks == 0)
    {
        g_opts.phases = 1;
    }
    if (g_opts.floor_y == INT32_MAX)
    {
        g_opts.floor_y = measure_lava_top();
    }
    setup_metric(player.get_position(), fht);
    std::fprintf(stderr, "goal %s at (%d, %d), %.0f px from spawn (progress baseline %.0f px); bands of %d px by %s x %d rock phases (cycle %d ticks); no credit below y=%d\n",
        g_have_goal ? (g_opts.goal.empty() ? "(farthest warp portal)" : g_opts.goal.c_str()) : "(none: x fallback)",
        g_goal_pos.x(), g_goal_pos.y(), g_spawndist, g_maxdist - 64.0, g_band_size, g_bands_by_y ? "height" : "column",
        g_opts.phases, g_opts.cycle_ticks, g_opts.floor_y);
    if (!g_opts.route.empty())
    {
        g_have_route = load_route(g_opts.route);
        if (g_have_route)
        {
            std::fprintf(stderr, "route table %s: %zu platforms, %zu ropes, up to %d hops; IJON slot = hops to goal\n",
                g_opts.route.c_str(), g_route_fh.size(), g_route_ladders.size(), g_route_max_hops);
        }
        else
        {
            std::fprintf(stderr, "route table %s not readable or empty: distance feedback\n", g_opts.route.c_str());
        }
    }

    if (g_opts.dump_map)
    {
        dump_map();
        std::fflush(g_out);
        _exit(0);
    }

    Offline::set_on_reset(on_reset);
    Offline::set_on_exit(on_exit);
    Offline::set_on_hit(on_hit);
    Offline::set_on_trap(on_trap);
    Offline::set_on_damage(on_damage);

    // One physics tick per input byte quarter. Returns true when the tick
    // budget ran out. `credit` controls IJON feedback (off for the prefix: its
    // maxima are the same in every run and would only add noise).
    auto run_bytes = [&](const std::vector<uint8_t>& bytes, bool credit) -> bool
    {
        for (uint8_t byte : bytes)
        {
            for (int32_t i = 0; i < Offline::TICKS_PER_BYTE; ++i)
            {
                Offline::apply_input(byte);
                Stage::get().update();
                g_tick++;

                Point<int16_t> pos = player.get_position();
                if (credit)
                {
                    feedback(pos, fht, player.get_phobj().onground || player.is_climbing(),
                        player.get_phobj().fhid, player.is_climbing());
                }
                if (g_opts.trace)
                {
                    std::fprintf(g_out, "T %d %d %d %d %d %.17g %.17g\n", g_tick, pos.x(), pos.y(),
                        ground_state(player), byte,
                        player.get_phobj().crnt_x(), player.get_phobj().crnt_y());
                    int32_t oid = 1000;
                    for (size_t m = 0; m < Offline::spawned_mobs().size(); ++m, ++oid)
                    {
                        Point<int16_t> mpos = Stage::get().get_mobs().get_mob_position(oid);
                        std::fprintf(g_out, "M %d %d %d %d\n", g_tick, oid, mpos.x(), mpos.y());
                    }
                }
                if (g_opts.death_y != INT32_MAX && pos.y() > g_opts.death_y)
                {
                    on_death(pos);
                }
                if (g_tick >= g_opts.max_ticks)
                {
                    return true;
                }
            }
        }
        return false;
    };

    // Checkpoint: bring the game to the frontier before the snapshot.
    std::vector<uint8_t> prefix = read_input(g_opts.prefix);
    if (!prefix.empty())
    {
        g_in_prefix = true;
        if (run_bytes(prefix, false))
        {
            prefix_failed("tick budget exhausted");
        }
        g_in_prefix = false;
        Point<int16_t> pos = player.get_position();
        std::fprintf(stderr, "prefix: %zu bytes, %d ticks, player at (%d, %d), hp %d\n",
            prefix.size(), g_tick, pos.x(), pos.y(), Offline::hp_left());
    }

    // Everything above is the deterministic snapshot. With afl-clang-fast the
    // forkserver starts here, so each run begins on the map, keys released,
    // or at the checkpoint when a prefix is given.
#ifdef __AFL_HAVE_MANUAL_CONTROL
    __AFL_INIT();
#endif

    std::vector<uint8_t> input = read_input(g_opts.input);

    if (g_opts.trace && prefix.empty())
    {
        Point<int16_t> pos = player.get_position();
        std::fprintf(g_out, "T %d %d %d %d %d\n", g_tick, pos.x(), pos.y(),
            ground_state(player), 0);
    }

    if (g_opts.via_replay)
    {
        // Browser path: Offline::tick() inside Stage::update applies the bytes.
        Offline::set_replay(input);
        while (Offline::replay_active() && g_tick < g_opts.max_ticks)
        {
            Stage::get().update();
            g_tick++;
            Point<int16_t> pos = player.get_position();
            feedback(pos, fht, player.get_phobj().onground || player.is_climbing(),
                player.get_phobj().fhid, player.is_climbing());
            if (g_opts.trace)
            {
                std::fprintf(g_out, "T %d %d %d %d %d\n", g_tick, pos.x(), pos.y(),
                    ground_state(player), 0);
            }
            if (g_opts.death_y != INT32_MAX && pos.y() > g_opts.death_y)
            {
                on_death(pos);
            }
        }
    }
    else
    {
        run_bytes(input, true);
    }
    // Key-free tail: the browser keeps simulating after a replay ends, and a
    // jump started by the last byte should get to land (and earn its credit,
    // or reach the goal) without the fuzzer having to grow the input first.
    // IJON trims each slot's input to end at its frontier, so without this
    // every next platform needed a jump byte plus a dozen bytes of tail.
    run_bytes(std::vector<uint8_t>(static_cast<size_t>(g_opts.tail_bytes), 0), true);
    {
        Point<int16_t> pos = player.get_position();
        std::fprintf(stderr, "END tick=%d x=%d y=%d progress=%d hp=%d resets=%d\n",
            g_tick, pos.x(), pos.y(), pos.x() - fht.get_walls().first(), Offline::hp_left(), g_reset_count);
    }
    std::fflush(g_out);
    // _exit skips static destructors (the client would write a Settings file
    // into the assets directory). Profilers need a normal exit to dump data.
    if (std::getenv("ZAKUM_NORMAL_EXIT"))
    {
        std::exit(0);
    }
    _exit(0);
}
