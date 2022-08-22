from typing import Dict, Tuple, List

from .cyclone import InterconnectGraph, Tile, SwitchBoxIO, Node, SwitchBoxNode, RegisterMuxNode, create_name, \
    SwitchBoxSide
from .circuit import TileCircuit
from .logic import ReadyValidGenerator

import kratos


class Interconnect(ReadyValidGenerator):
    def __init__(self, interconnects: Dict[int, InterconnectGraph],
                 config_addr_width: int = 8, config_data_width: int = 32,
                 full_config_addr_width: int = 32, tile_id_width: int = 16,
                 lift_ports=False):
        super().__init__("Interconnect")
        self.config_data_width = config_data_width
        self.config_addr_width = config_addr_width
        self.tile_id_width = tile_id_width
        self.__graphs: Dict[int, InterconnectGraph] = interconnects
        self.__lifted_ports = lift_ports

        self.__tiles: Dict[Tuple[int, int], Dict[int, Tile]] = {}
        self.tile_circuits: Dict[Tuple[int, int], TileCircuit] = {}

        # loop through the grid and create tile circuits
        # first find all the coordinates
        coordinates = []
        for _, graph in self.__graphs.items():
            for coord in graph:
                if coord not in coordinates:
                    coordinates.append(coord)
        # add tiles
        x_min = 0xFFFF
        x_max = -1
        y_min = 0xFFFF
        y_max = -1

        for x, y in coordinates:
            for bit_width, graph in self.__graphs.items():
                if graph.is_original_tile(x, y):
                    tile = graph[(x, y)]
                    if (x, y) not in self.__tiles:
                        self.__tiles[(x, y)] = {}
                    self.__tiles[(x, y)][bit_width] = tile

            # set the dimensions
            if x > x_max:
                x_max = x
            if x < x_min:
                x_min = x
            if y > y_max:
                y_max = y
            if y < y_min:
                y_min = y
        assert x_max >= x_min
        assert y_max >= y_min

        self.x_min, self.x_max = x_min, x_max
        self.y_min, self.y_max = y_min, y_max

        # create individual tile circuits
        for coord, tiles in self.__tiles.items():
            tile = TileCircuit(tiles, config_addr_width, config_data_width,
                               tile_id_width=tile_id_width, full_config_addr_width=full_config_addr_width)
            self.tile_circuits[coord] = tile
            x, y = coord
            self.add_child("Tile_X{0:02X}Y{1:02X}".format(x, y), tile)

        self.__wire_tiles()

        # connect these margin tiles, if needed
        self.__connect_margin_tiles()

        # if we need to lift the ports. this can be used for testing or
        # creating circuit without IO
        if lift_ports:
            self.__lift_ports()
        else:
            self.__ground_ports()

        # clean up empty tiles
        self.__cleanup_tiles()

        # set tile_id
        self.__set_tile_id()

        self.clk = self.clock("clk")
        self.reset = self.reset("rst_n", active_high=False)
        self.clk_en = self.clock_en("clk_en")
        self.config_data = self.input("config_data", self.config_data_width)
        self.config_addr = self.input("config_addr", full_config_addr_width)

    def __wire_tiles(self):
        for (x, y), tile in self.tile_circuits.items():
            for bit_width, switch_box in tile.sbs.items():
                all_sbs = switch_box.switchbox.get_all_sbs()
                for sb in all_sbs:
                    if sb.io != SwitchBoxIO.SB_OUT:
                        continue
                    assert x == sb.x and y == sb.y
                    # we need to be carefully about looping through the
                    # connections
                    # if the switch box has pipeline registers, we need to
                    # do a "jump" over the connected switch
                    # format: dst_node, src_port_name, src_node
                    neighbors: List[Tuple[Node, str, Node]] = []
                    for node in sb:
                        if isinstance(node, SwitchBoxNode):
                            neighbors.append((node, create_name(str(sb)), sb))
                        elif isinstance(node, RegisterMuxNode):
                            # making sure the register is inserted properly
                            assert len(sb) == 2
                            # we need to make a jump here
                            for n in node:
                                neighbors.clear()
                                if isinstance(n, SwitchBoxNode):
                                    neighbors.append((n, create_name(str(sb)),
                                                      node))
                            break
                    for sb_node, src_sb_name, src_node in neighbors:
                        assert isinstance(sb_node, SwitchBoxNode)
                        assert sb_node.io == SwitchBoxIO.SB_IN
                        # notice that we already lift the ports up
                        # since we are not dealing with internal connections
                        # using the tile-level port is fine
                        dst_tile = self.tile_circuits[(sb_node.x, sb_node.y)]
                        # wire them up
                        dst_sb_name = create_name(str(sb_node))
                        assert len(sb_node.get_conn_in()) == 1, \
                            "Currently only one to one allowed for inter-tile connections"
                        # no array
                        tile_port = tile.ports[src_sb_name]
                        dst_port = dst_tile.ports[dst_sb_name]
                        self.wire_rv(tile_port, dst_port)

    def get_tile_id(self, x: int, y: int):
        return x << (self.tile_id_width // 2) | y

    def __set_tile_id(self):
        for (x, y), tile in self.tile_circuits.items():
            tile_id = self.get_tile_id(x, y)
            self.add_stmt(tile.tile_id.assign(tile_id))

    def get_config_addr(self, reg_addr: int, feat_addr: int, x: int, y: int):
        tile_id = self.get_tile_id(x, y)
        tile = self.tile_circuits[(x, y)]
        addr = (reg_addr << tile.feature_config_slice.start) | \
               (feat_addr << tile.tile_id_width)
        addr = addr | tile_id
        return addr

    def __connect_margin_tiles(self):
        # connect these margin tiles
        # margin tiles have empty switchbox
        for coord, tile_dict in self.__tiles.items():
            for bit_width, tile in tile_dict.items():
                if tile.switchbox.num_track > 0 or tile.core is None:
                    continue
                for port_name, port_node in tile.ports.items():
                    tile_port = self.tile_circuits[coord].ports[port_name]
                    if len(port_node) == 0 and len(port_node.get_conn_in()) == 0:
                        # lift this port up
                        x, y = coord
                        new_port_name = f"{port_name}_X{x:02X}_Y{y:02X}"
                        self.lift_rv(tile_port, new_port_name)
                    else:
                        # connect them to the internal fabric
                        nodes = list(port_node) + port_node.get_conn_in()[:]
                        for sb_node in nodes:
                            next_coord = sb_node.x, sb_node.y
                            next_node = sb_node
                            # depends on whether there is a pipeline register
                            # or not, we need to be very careful
                            if isinstance(sb_node, SwitchBoxNode):
                                sb_name = create_name(str(sb_node))
                            else:
                                assert isinstance(sb_node, RegisterMuxNode)
                                # because margin tiles won't connect to
                                # reg mux node, they can only be connected
                                # from
                                nodes = sb_node.get_conn_in()[:]
                                nodes = [x for x in nodes if
                                         isinstance(x, SwitchBoxNode)]
                                assert len(nodes) == 1
                                sb_node = nodes[0]
                                sb_name = create_name(str(sb_node))

                            next_port = self.tile_circuits[next_coord].ports[sb_name]
                            if len(port_node.get_conn_in()) <= 1:
                                self.wire_rv(tile_port, next_port)
                            else:
                                raise NotImplemented("Fanout on margin tile not supported. Use a SB instead")

    def __lift_ports(self):
        # we assume it's a rectangular grid
        # we only care about the perimeter
        x_range = {self.x_min, self.x_max}
        y_range = {self.y_min, self.y_max}
        coordinates = []
        for (x, y) in self.tile_circuits:
            if x in x_range or y in y_range:
                coord = (x, y)
                if coord not in coordinates:
                    coordinates.append((x, y))
        for x, y in coordinates:
            tile = self.tile_circuits[(x, y)]
            # we only lift sb ports
            sbs = tile.sbs
            for bit_width, switchbox in sbs.items():
                all_sbs = switchbox.switchbox.get_all_sbs()
                working_set = []
                if x == self.x_min:
                    # we lift west/left ports
                    for sb_node in all_sbs:
                        if sb_node.side != SwitchBoxSide.WEST:
                            continue
                        working_set.append(sb_node)
                if x == self.x_max:
                    # we lift east/right ports
                    for sb_node in all_sbs:
                        if sb_node.side != SwitchBoxSide.EAST:
                            continue
                        working_set.append(sb_node)
                if y == self.y_min:
                    # we lift north/top ports
                    for sb_node in all_sbs:
                        if sb_node.side != SwitchBoxSide.NORTH:
                            continue
                        working_set.append(sb_node)
                if y == self.y_max:
                    # we lift south/bottom ports
                    for sb_node in all_sbs:
                        if sb_node.side != SwitchBoxSide.SOUTH:
                            continue
                        working_set.append(sb_node)
                for sb_node in working_set:
                    sb_name = create_name(str(sb_node))
                    sb_port = tile.ports[sb_name]
                    # because the lifted port will conflict with each other
                    # we need to add x and y to the sb_name to avoid conflict
                    new_sb_name = sb_name + f"_X{sb_node.x}_Y{sb_node.y}"
                    self.lift_rv(sb_port, new_sb_name)

    def __ground_ports(self):
        # this is a pass to ground every sb ports that's not connected
        for coord, tile_dict in self.__tiles.items():
            for bit_width, tile in tile_dict.items():
                for sb in tile.switchbox.get_all_sbs():
                    sb_name = create_name(str(sb))
                    sb_port = self.tile_circuits[coord].ports[sb_name]
                    if sb.io == SwitchBoxIO.SB_IN:
                        if sb.get_conn_in():
                            continue
                        # no connection to that sb port, ground it
                        self.__wire_ground(sb_port)
                    else:
                        margin = False
                        if len(sb) > 0:
                            for n in sb:
                                if isinstance(n, RegisterMuxNode):
                                    margin = len(n) == 0
                        else:
                            margin = True
                        if not margin:
                            continue
                        self.__wire_ground(sb_port)

    def __wire_ground(self, port: kratos.Port):
        if port.port_direction == kratos.PortDirection.In:
            ready_port = port.generator.get_port(port.name + "_ready")
            self.wire(ready_port, kratos.const(0))
        else:
            self.wire(port, kratos.const(0))
            valid_port = port.generator.get_port(port.name + "_valid")
            self.wire(valid_port, kratos.const(0))

    def __cleanup_tiles(self):
        tiles_to_remove = set()
        for coord, tile in self.tile_circuits.items():
            if tile.core is None:
                tiles_to_remove.add(coord)

        # remove empty tiles
        for coord in tiles_to_remove:
            # remove the tile id as well
            tile_circuit = self.tile_circuits[coord]
            self.remove_child_generator(tile_circuit)
            self.tile_circuits.pop(coord)

    def finalize(self):
        # optimization using clone?
        for tile_circuit in self.tile_circuits.values():
            if "clk" in tile_circuit.ports:
                self.wire(self.clk, tile_circuit.clk)
                self.wire(self.clk_en, tile_circuit.clk_en)
                self.wire(self.reset, tile_circuit.reset)
                self.wire(self.config_addr, tile_circuit.config_addr)
                self.wire(self.config_data, tile_circuit.config_data)
            tile_circuit.finalize()
