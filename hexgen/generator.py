import math
from app import session
from enum import Enum
from app.generators.hexgen.grid import GridGen
from app.generators.hexgen.hex import HexFeature
from app.generators.hexgen.constants import *
from app.generators.hexgen.draw import HexGridDraw
from app.decorators import exec_time
import random
from app.generators.namegen import Namegen
from app.models.game.province import Province, Hex
from app.models.universe.world import WorldType


@exec_time
def draw_grid(hex_grid):
    def color_heightmap(hex):
        alt = hex.altitude
        return alt, alt, alt

    # make terrain map
    def color_terrain(hex):
        return hex.color_terrain

    def color_rivers(hex):
        return hex.color_rivers

    def color_temperature(hex):
        return hex.color_temperature

    def color_biome(hex):
        return hex.color_biome

    def color_territories(hex):
        return hex.color_territories

    def color_satellite(hex):
        return hex.color_satellite

    def color_features(hex):
        if hex.has_feature(HexFeature.lava_flow):
            return (200, 100, 0)
        if hex.has_feature(HexFeature.volcano):
            return (255, 0, 0)
        if hex.has_feature(HexFeature.crater):
            return (255, 255, 0)
        return (200, 200, 200)

    def color_resources(hex):
        if hex.resource is not None:
            return hex.resource.get('type').color
        return (100, 100, 100)

    HexGridDraw(hex_grid, color_features, "map_features.png", show_coasts=True, rivers=False)
    HexGridDraw(hex_grid, color_heightmap, "map_height.png", rivers=False, show_coasts=True)
    HexGridDraw(hex_grid, color_terrain, "map_terrain.png", rivers=True)
    HexGridDraw(hex_grid, color_rivers, "map_rivers.png", rivers=True)
    HexGridDraw(hex_grid, color_temperature, "map_temp.png", rivers=False, show_coasts=True)
    HexGridDraw(hex_grid, color_biome, "map_biome.png", rivers=False)
    HexGridDraw(hex_grid, color_territories, "map_territories.png", rivers=False,
                show_coasts=True, borders=True)
    HexGridDraw(hex_grid, color_satellite, "map_satellite.png")
    HexGridDraw(hex_grid, color_resources, "map_resources.png")

    # report on territories
    for t in hex_grid.territories:
        print("Territory {}:\n"
              "\tSize: {}\n"
              "\tColor: {}\n"
              "\tLandlocked: {}\n"
              "\tAverage Temperature: {}\n"
              "\tAverage Moisture: {}\n"
              "\tNeighbors: {}"
              .format(t.id, t.size, t.color, t.landlocked, t.avg_temp, t.avg_moisture,
                      t.neighbors))
        print("\tBiomes:")
        for b in t.biomes:
            print("\t - {}: {} - {}%".format(b.get('biome').title,
                                             b.get('count'),
                                             round((b.get('count') / t.size) * 100, 2)))
        print("\tGroups: {}".format(len(t.groups)))
        for g in t.groups:
            print("\t\tHexes: {}, X: {}, Y: {}".format(g.get('size'), g.get('x'), g.get('y')))

@exec_time
def save_grid(colony, hex_grid, debug=False):
    """
    Saves a hex_grid to the database
    :param colony: Colony instance
    :param hex_grid: HexGrid instance
    :return: True or False on success
    """
    # cleans up
    if len(colony.provinces) is not None:
        print("Removing previous territories and hexes") if debug else False
        colony.provinces = []
        colony.hexes = []
        colony.save()

    print("Saving world map data to colony") if debug else False
    colony.world_map_config = dict(sea_level=hex_grid.sealevel,
                                   max_height=hex_grid.top_height)
    colony.save()

    print("Saving provinces") if debug else False
    provinces = []

    # first pass, make instances
    for t in hex_grid.territories:
        gen_name = Namegen('states').generate_name()
        p = Province(name=gen_name,
                     avg_temp=t.avg_temp,
                     avg_rainfall=t.avg_moisture,
                     landlocked=t.landlocked)
        p.color = t.color
        p.colony = colony
        p._groups = ';'.join([str(i.get('size'))+','+str(i.get('x'))+','+str(i.get('y')) for i in t.groups])
        t.db_instance = p
        session.add(p)

    # make neighbors
    print("Saving neighboring province lists") if debug else False
    for t in hex_grid.territories:
        t.db_instance.neighbors = [tn.db_instance for tn in t.neighbors]

    # generate hex instances
    print("Saving hexagons (will take a while)") if debug else False
    for y, row in enumerate(hex_grid.hex_grid.grid):
        for x, col in enumerate(row):
            hex_inst = hex_grid.hex_grid.find_hex(x, y)

            n_hex = Hex(x=x, y=y, altitude=hex_inst.altitude,
                        rainfall=hex_inst.moisture,
                        temperature=hex_inst.temperature,
                        biome=hex_inst.biome,
                        color_terrain=hex_inst.color_terrain,
                        color_rainfall=hex_inst.color_rivers,
                        color_temperature=hex_inst.color_temperature,
                        color_biome=hex_inst.color_biome,
                        color_satellite=hex_inst.color_satellite)

            if hex_inst.resource is not None:
                hex_inst.resource_rating = hex_inst.resource.get('rating')
                hex_inst.resource_type = hex_inst.resource.get('type')

            # is this a coastal hex?
            for e in hex_inst.edges:
                edge_t = set()
                edge_t.add(0)
                if hex_inst.is_land and e.two.is_water:
                    edge_t.add(2)
                if e.one.is_owned and e.two.is_owned and e.one.territory.id != e.two.territory.id:
                    edge_t.add(4)
                setattr(n_hex, 'edge_'+e.side.name, tuple(edge_t))

            segments = hex_grid.find_river(x, y)
            for s in segments:
                edge_t = set(getattr(n_hex, 'edge_'+s.name))
                edge_t.add(1)
                setattr(n_hex, 'edge_'+s.name, tuple(edge_t))



            if hex_inst.is_owned:
                n_hex.province = hex_inst.territory.db_instance
            n_hex.colony = colony
            session.add(n_hex)

    session.commit()

@exec_time
def generate(colony, debug=False, save=True):
    """
    Given a colony, creates a world map
    :param colony: Colony instance
    :return: True or False on success
    """

    # generate random values
    sea_percent = random.randint(50, 70)
    avg_temp = colony.world.avg_temp_celsius #random.randint(14, 25)
    size = colony.world.size.size_hex

    if debug:
        print("Making world with Sea Percent: {} and Avg Temp: {}".format(sea_percent, avg_temp))
    hex_grid = GridGen(colony, size, avg_surface_temp=avg_temp, debug=debug)
    if save:
        save_grid(colony, hex_grid, debug=debug)

    if debug:
        draw_grid(hex_grid)