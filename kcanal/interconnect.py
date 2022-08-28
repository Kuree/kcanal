from typing import Dict, Tuple, List

from .cyclone import InterconnectGraph, Tile, SwitchBoxIO, Node, SwitchBoxNode, RegisterMuxNode, create_name, \
    SwitchBoxSide, PortNode
from .circuit import TileCircuit
from .logic import ReadyValidGenerator
from .pnr import PnRTag

import kratos
import os


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
        unique_tiles: Dict[str, TileCircuit] = {}
        for coord, tiles in self.__tiles.items():
            tile = TileCircuit(tiles, config_addr_width, config_data_width,
                               tile_id_width=tile_id_width, full_config_addr_width=full_config_addr_width)
            self.tile_circuits[coord] = tile
            if tile.name in unique_tiles:
                ref = unique_tiles[tile.name]
                tile.internal_generator.copy_over_missing_ports(ref.internal_generator)
                tile.internal_generator.set_clone_ref(ref.internal_generator)
            else:
                tile.lift_ports()
                unique_tiles[tile.name] = tile
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
        for coord, tile_dict in self.__tiles.items():
            x, y = coord
            tile = self.tile_circuits[(x, y)]
            # we only lift sb ports
            sbs = tile.sbs
            for bit_width, switchbox in sbs.items():
                all_sbs = switchbox.switchbox.get_all_sbs()
                for sb in all_sbs:
                    sb_name = create_name(str(sb))
                    sb_port = self.tile_circuits[coord].ports[sb_name]
                    if sb.io == SwitchBoxIO.SB_IN:
                        if len(sb.get_conn_in()) > 0:
                            continue
                        # because the lifted port will conflict with each other
                        # we need to add x and y to the sb_name to avoid conflict
                        new_sb_name = sb_name + f"_X{x}_Y{y}"
                        self.lift_rv(sb_port, new_sb_name)
                    else:
                        # make sure the connected nodes doesn't have any nodes
                        # need to bypass rmux if possible
                        connected = False
                        for node in sb:
                            for n in node:
                                if n.x != x or n.y != y:
                                    connected = True
                                    break
                        if not connected:
                            new_sb_name = sb_name + f"_X{x}_Y{y}"
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
        # we assume that users knows what's going on with the tile definition
        definition_tiles: Dict[str, TileCircuit] = {}
        for tile_circuit in self.tile_circuits.values():
            if tile_circuit.name in definition_tiles:
                ref = definition_tiles[tile_circuit.name].internal_generator
                tile_circuit.internal_generator.set_clone_ref(ref)
                continue
            if "clk" in tile_circuit.ports:
                self.wire(self.clk, tile_circuit.clk)
                self.wire(self.clk_en, tile_circuit.clk_en)
                self.wire(self.reset, tile_circuit.reset)
                self.wire(self.config_addr, tile_circuit.config_addr)
                self.wire(self.config_data, tile_circuit.config_data)
            tile_circuit.finalize()
            definition_tiles[tile_circuit.name] = tile_circuit

    # software interaction
    def dump_pnr(self, dir_name, design_name, max_num_col=None):
        if not os.path.isdir(dir_name):
            os.mkdir(dir_name)
        dir_name = os.path.abspath(dir_name)
        if max_num_col is None:
            max_num_col = self.x_max + 1

        graph_path_dict = {}
        for bit_width, graph in self.__graphs.items():
            graph_path = os.path.join(dir_name, f"{bit_width}.graph")
            graph_path_dict[bit_width] = graph_path
            graph.dump_graph(graph_path, max_num_col)

        # generate the layout file
        layout_file = os.path.join(dir_name, f"{design_name}.layout")
        self.__dump_layout_file(layout_file, max_num_col)
        pnr_file = os.path.join(dir_name, f"{design_name}.info")
        with open(pnr_file, "w+") as f:
            f.write(f"layout={layout_file}\n")
            graph_configs = [f"{bit_width} {graph_path_dict[bit_width]}" for
                             bit_width in self.__graphs]
            graph_config_str = " ".join(graph_configs)
            f.write(f"graph={graph_config_str}\n")

    def __get_core_info(self) -> Dict[str, Tuple[PnRTag, List[PnRTag]]]:
        result = {}
        for coord in self.tile_circuits:
            tile = self.tile_circuits[coord]
            cores = [tile.core] + tile.additional_cores
            for core in cores:
                info = core.pnr_info()
                core_name = core.name
                if core_name not in result:
                    result[core_name] = info
                else:
                    assert result[core_name] == info
        return result

    @staticmethod
    def __get_core_tag(core_info):
        name_to_tag = {}
        tag_to_name = {}
        tag_to_priority = {}
        for core_name, tags in core_info.items():
            if not isinstance(tags, list):
                tags = [tags]
            for tag in tags:  # type: PnRTag
                tag_name = tag.tag_name
                if core_name not in name_to_tag:
                    name_to_tag[core_name] = []
                name_to_tag[core_name].append(tag.tag_name)
                assert tag_name not in tag_to_name, f"{tag_name} already exists"
                tag_to_name[tag_name] = core_name
                tag_to_priority[tag_name] = (tag.priority_major,
                                             tag.priority_minor)
        return name_to_tag, tag_to_name, tag_to_priority

    def __get_registered_tile(self):
        result = set()
        for coord, tile_circuit in self.tile_circuits.items():
            for _, tile in tile_circuit.tiles.items():
                switchbox = tile.switchbox
                if len(switchbox.registers) > 0:
                    result.add(coord)
                    break
        return result

    def __dump_layout_file(self, layout_file, max_num_col):
        # empty tiles first
        with open(layout_file, "w+") as f:
            f.write("LAYOUT   0 20\nBEGIN\n")
            for y in range(self.y_max + 1):
                for x in range(max_num_col):
                    coord = (x, y)
                    if coord not in self.tile_circuits:
                        f.write("1")
                    else:
                        f.write("0")
                f.write("\n")
            f.write("END\n")
            # looping through the tiles to figure what core it has
            # use default priority 20
            default_priority = 20
            core_info = self.__get_core_info()
            name_to_tag, tag_to_name, tag_to_priority \
                = self.__get_core_tag(core_info)
            for core_name, tags in name_to_tag.items():
                for tag in tags:
                    priority_major, priority_minor = tag_to_priority[tag]
                    f.write(f"LAYOUT {tag} {priority_major} {priority_minor}\n")
                    f.write("BEGIN\n")
                    for y in range(self.y_max + 1):
                        for x in range(max_num_col):
                            coord = (x, y)
                            if coord not in self.tile_circuits:
                                f.write("0")
                            else:
                                tile = self.tile_circuits[coord]
                                cores = [tile.core] + tile.additional_cores
                                core_names = [core.name for core in cores]
                                if core_name not in core_names:
                                    f.write("0")
                                else:
                                    f.write("1")
                        f.write("\n")
                    f.write("END\n")
            # handle registers
            assert "r" not in tag_to_name
            r_locs = self.__get_registered_tile()
            f.write(f"LAYOUT r {default_priority} 0\nBEGIN\n")
            for y in range(self.y_max + 1):
                for x in range(max_num_col):
                    if (x, y) in r_locs:
                        f.write("1")
                    else:
                        f.write("0")
                f.write("\n")
            f.write("END\n")

    def parse_node(self, node_str):
        if node_str[0] == "SB":
            track, x, y, side, io_, bit_width = node_str[1:]
            graph = self.get_graph(bit_width)
            return graph.get_sb(x, y, SwitchBoxSide(side), track,
                                SwitchBoxIO(io_))
        elif node_str[0] == "PORT":
            port_name, x, y, bit_width = node_str[1:]
            graph = self.get_graph(bit_width)
            return graph.get_port(x, y, port_name)
        elif node_str[0] == "REG":
            reg_name, track, x, y, bit_width = node_str[1:]
            graph = self.get_graph(bit_width)
            return graph.get_tile(x, y).switchbox.registers[reg_name]
        elif node_str[0] == "RMUX":
            rmux_name, x, y, bit_width = node_str[1:]
            graph = self.get_graph(bit_width)
            return graph.get_tile(x, y).switchbox.reg_muxs[rmux_name]
        else:
            raise Exception("Unknown node " + " ".join(node_str))

    def get_route_bitstream(self, routes: Dict[str, List[List[Node]]]):
        result = []
        for _, route in routes.items():
            for segment in route:
                for i in range(len(segment) - 1):
                    pre_node = segment[i]
                    next_node = segment[i + 1]
                    assert next_node in pre_node
                    # notice that there is a corner case where the SB directly connect to the
                    # next tile's CB
                    if (pre_node.x != next_node.x or pre_node.y != next_node.y) and \
                            (not (isinstance(pre_node, RegisterMuxNode) and isinstance(next_node, PortNode))):
                        # inter tile connection. skipping for now
                        continue
                    if len(next_node.get_conn_in()) == 1:
                        # no mux created. skip
                        continue
                    configs = self.get_node_bitstream_config(pre_node,
                                                             next_node)
                    for addr, data in configs:
                        result.append((addr, data))
        return result

    def get_node_bitstream_config(self, src_node: Node, dst_node: Node, ):
        # this is the complete one which includes the tile_id
        x, y = dst_node.x, dst_node.y
        tile = self.tile_circuits[(x, y)]
        res = []
        configs = tile.get_route_bitstream_config(src_node, dst_node)

        def add_config(entry):
            reg_addr, feat_addr, data = entry
            addr = self.get_config_addr(reg_addr, feat_addr, x, y)
            res.append((addr, data))

        if isinstance(configs, list):
            for en in configs:
                add_config(en)
        else:
            add_config(configs)

        return res

    def get_graph(self, bit_width: int):
        return self.__graphs[bit_width]
