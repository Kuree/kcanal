import kratos
import _kratos

from typing import List, Dict, Tuple, Union
from .cyclone import InterconnectCore, PortNode, Node, SwitchBox, RegisterNode, RegisterMuxNode, SwitchBoxNode, \
    SwitchBoxIO, ImranSwitchBox, Tile
from .logic import Configurable, Mux, FIFO, ReadyValidGenerator


class Core(ReadyValidGenerator, InterconnectCore):
    def __init__(self, name: str, debug: bool = False):
        super(Core, self).__init__(name, debug)

        self.__input_ports: List[kratos.Port] = []
        self.__output_ports: List[kratos.Port] = []

    def inputs(self) -> List[kratos.Port]:
        return self.__input_ports

    def outputs(self) -> List[kratos.Port]:
        return self.__output_ports

    def core_name(self) -> str:
        return self.name


def create_name(name: str):
    tokens = " (),"
    for t in tokens:
        name = name.replace(t, "_")
    name = name.replace("__", "_")
    if name[-1] == "_":
        name = name[:-1]
    return name


def _create_mux(node: Node):
    conn_in = node.get_conn_in()
    height = len(conn_in)
    if height == 0:
        height = 1
    mux = Mux(height, node.width)
    return mux


class CB(Configurable):
    def __init__(self, node: PortNode, config_addr_width: int, config_data_width: int, debug: bool = False):
        self.node = node
        self.width = node.width
        super(CB, self).__init__(create_name(str(node)), config_addr_width, config_data_width, debug=debug)

        self.mux = _create_mux(node)
        self.in_ = self.input("I", self.width, size=[self.mux.height], packed=True)
        self.out_ = self.output("O", self.width)
        self.sel = self.add_config("sel", self.mux.sel.width)
        self.en = self.add_config("en", self.mux.en.width)
        self.valid_in = self.port_from_def(self.mux.valid_in)
        self.valid_out = self.port_from_def(self.mux.valid_out)
        self.ready_in = self.port_from_def(self.mux.ready_in)
        self.ready_out = self.port_from_def(self.mux.ready_out)

        self.add_child("mux", self.mux,
                       I=self.in_, O=self.out_, S=self.sel, valid_in=self.valid_in, valid_out=self.valid_out,
                       ready_in=self.ready_in, ready_out=self.ready_out, enable=self.en)


class SB(Configurable):
    def __init__(self, switchbox: SwitchBox, config_addr_width: int, config_data_width: int, core_name: str,
                 debug: bool = False):
        name = f"SB_ID{switchbox.id}_{switchbox.num_track}TRACKS_B{switchbox.width}_{core_name}"
        super(SB, self).__init__(name, config_addr_width, config_data_width, debug=debug)
        self.switchbox = switchbox
        self.clk_en = self.clock_en("clk_en", 1)

        self.sb_muxs: Dict[str, Tuple[SwitchBoxNode, Mux]] = {}
        self.regs: Dict[str, Tuple[RegisterNode, FIFO]] = {}
        self.reg_muxs: Dict[str, Tuple[RegisterMuxNode, Mux]] = {}

        self.__create_sb_mux()
        self.__create_regs()
        self.__create_reg_mux()
        # connect internal sbs
        self.__connect_sbs()

        # connect regs and reg muxs
        # we need to make three connections in total
        #      REG
        #  1 /    \ 3
        # SB ______ MUX
        #       2
        self.__connect_sb_out()
        self.__connect_regs()

        self.__connect_sb_in()

        self.__add_config_reg()
        self.__handle_reg_clk_en()

        self.__lift_ports()
        self.__handle_port_connection()

    def __create_sb_mux(self):
        sbs = self.switchbox.get_all_sbs()
        for sb in sbs:
            sb_name = str(sb)
            mux = _create_mux(sb)
            self.add_child("MUX_" + create_name(sb_name), mux)
            self.sb_muxs[sb_name] = (sb, mux)

    def __create_regs(self):
        for reg_name, reg_node in self.switchbox.registers.items():
            reg = FIFO(self.switchbox.width, 2)
            inst_name = create_name(str(reg_node))
            self.add_child(inst_name, reg, clk=self.clk)
            self.regs[reg_name] = reg_node, reg

    def __create_reg_mux(self):
        for _, reg_mux in self.switchbox.reg_muxs.items():
            # assert the connections to make sure it's a valid register
            # mux
            conn_ins = reg_mux.get_conn_in()
            assert len(conn_ins) == 2
            # find out the sb it's connected in. also do some checking
            node1, node2 = conn_ins
            if isinstance(node1, RegisterNode):
                assert isinstance(node2, SwitchBoxNode)
                assert node2.io == SwitchBoxIO.SB_OUT
                sb_node = node2
            elif isinstance(node2, RegisterNode):
                assert isinstance(node1, SwitchBoxNode)
                assert node1.io == SwitchBoxIO.SB_OUT
                sb_node = node1
            else:
                raise ValueError("expect a sb connected to the reg_mux")
            # we use the sb_name instead so that when we lift the port up,
            # we can use the mux output instead
            sb_name = str(sb_node)
            self.reg_muxs[sb_name] = (reg_mux, _create_mux(reg_mux))

    def __lift_ports(self):
        for sb_name, (sb, mux) in self.sb_muxs.items():
            # only lift them if the ports are connect to the outside world
            port_name = create_name(sb_name)
            # ready valid interface
            ready_name = f"{port_name}_ready"
            valid_name = f"{port_name}_valid"
            if sb.io == SwitchBoxIO.SB_IN:
                p, r, v = self.port_from_def_rv(mux.in_, port_name, check_param=False)
                self.wire(p, mux.in_)
                self.wire(r, mux.ready_out)
                self.wire(v, mux.valid_in)
            else:
                # to see if we have a register mux here
                # if so , we need to lift the reg_mux output instead
                if sb_name in self.reg_muxs:
                    # override the mux value
                    sb_mux = mux
                    node, mux = self.reg_muxs[sb_name]
                    assert isinstance(node, RegisterMuxNode)
                    assert node in sb
                    #     /-- reg--\
                    # sb /          | rmux
                    #    \---------/
                    p = self.var(f"{sb_name}_ready_merge", 1)
                    reg_node: Union[RegisterNode, None] = None
                    for reg_node in sb:
                        if isinstance(reg_node, RegisterNode):
                            break
                    assert reg_node is not None
                    conn_in = node.get_conn_in()
                    rmux_idx = conn_in.index(node)
                    reg_idx = conn_in.index(reg_node)
                    reg = self.regs[reg_node.name][1]
                    self.wire(p, (mux.ready_out & mux.sel_out[rmux_idx]) | (reg.ready_out & mux.sel_out[reg_idx]))
                    self.wire(p, sb_mux.ready_in)
                else:
                    p = self.port_from_def(mux.ready_in, ready_name)
                    self.wire(p, mux.ready_in)
                p = self.port_from_def(mux.out_, port_name, check_param=False)
                self.wire(p, mux.out_)

                p = self.port_from_def(mux.valid_out, valid_name)
                self.wire(p, mux.valid_out)

    def __connect_sbs(self):
        # the principle is that it only connects to the nodes within
        # its range. for instance, in SB we only connect to sb nodes
        for _, (sb, mux) in self.sb_muxs.items():
            if sb.io == SwitchBoxIO.SB_IN:
                for node in sb:
                    if isinstance(node, SwitchBoxNode):
                        assert node.io == SwitchBoxIO.SB_OUT
                        assert node.x == sb.x and node.y == sb.y
                        output_port = mux.out_
                        idx = node.get_conn_in().index(sb)
                        node_, node_mux = self.sb_muxs[str(node)]
                        assert node_ == node
                        input_port = node_mux.in_[idx]
                        self.wire(input_port, output_port)
                        self.wire(node_mux.valid_in[idx], mux.valid_out)

    def __connect_sb_out(self):
        for _, (sb, mux) in self.sb_muxs.items():
            if sb.io == SwitchBoxIO.SB_OUT:
                for node in sb:
                    if isinstance(node, RegisterNode):
                        reg_name = node.name
                        reg_node, reg = self.regs[reg_name]
                        assert len(reg_node.get_conn_in()) == 1
                        # wire 1
                        self.wire(mux.out_, reg.data_in)
                        self.wire(mux.valid_out, reg.push)
                    elif isinstance(node, RegisterMuxNode):
                        assert len(node.get_conn_in()) == 2
                        idx = node.get_conn_in().index(sb)
                        sb_name = str(sb)
                        n, reg_mux = self.reg_muxs[sb_name]
                        assert n == node
                        # wire 2
                        self.wire(mux.out_, reg_mux.in_[idx])
                        self.wire(mux.valid_out, reg_mux.valid_in[idx])

    def __connect_regs(self):
        for _, (node, reg) in self.regs.items():
            assert len(node) == 1, "pipeline register only has 1 connection"
            reg_mux_node: RegisterMuxNode = list(node)[0]
            # make a copy since we need to pop the list
            reg_mux_conn = reg_mux_node.get_conn_in()[:]
            assert len(reg_mux_conn) == 2, "register mux can only have 2 incoming connections"
            reg_mux_conn.remove(node)
            assert isinstance(reg_mux_conn[0], SwitchBoxNode)
            sb_node: Node = reg_mux_conn[0]
            assert node in sb_node, "register has to be connected together with a reg mux"
            sb_name = str(sb_node)
            n, mux = self.reg_muxs[sb_name]
            assert n == reg_mux_node
            idx = reg_mux_node.get_conn_in().index(node)
            # wire 3
            self.wire(reg.data_out, mux.in_[idx])

            # need to connect valid signals
            self.wire(reg.valid_out, mux.valid_in[idx])
            self.wire(reg.push, mux.ready_out)

    def __handle_port_connection(self):
        for _, (sb, mux) in self.sb_muxs.items():
            if sb.io != SwitchBoxIO.SB_OUT:
                continue
            nodes_from = sb.get_conn_in()
            for idx, node in enumerate(nodes_from):
                if not isinstance(node, PortNode):
                    continue
                _, r, v = self.input_rv(node.name, node.width)
                self.wire(r, mux.ready_out)
                self.wire(v, mux.valid_in[idx])

    def __connect_sb_in(self):
        for _, (sb, sb_mux) in self.sb_muxs.items():
            if sb.io != SwitchBoxIO.SB_IN:
                continue
            nodes = list(sb)
            # need to merge the ready in properly
            sb_name = create_name(str(sb))
            merge = self.var(f"{sb_name}_ready_merge", 1)
            merge_vars = []
            for node in nodes:
                idx = node.get_conn_in().index(sb)
                if isinstance(node, SwitchBoxNode):
                    # make sure it's a mux
                    assert len(node.get_conn_in()) > 1, "Invalid routing topology"
                    mux = self.sb_muxs[str(node)][-1]
                    ready = mux.sel_out[idx] & mux.ready_out[idx]
                    merge_vars.append(ready)
                else:
                    assert isinstance(node, PortNode)
                    sel_out_name = node.name + "_sel_out"
                    if sel_out_name in self.ports:
                        p = self.ports[sel_out_name]
                    else:
                        p = self.input(sel_out_name, len(node.get_conn_in()))
                    merge_vars.append(p[idx])
            self.wire(merge, kratos.util.reduce_or(*merge_vars))
            self.wire(sb_mux.ready_in, merge)

    @staticmethod
    def get_mux_sel_name(node: Node):
        name = create_name(str(node))
        sel = f"{name}_sel"
        en = f"{name}_en"
        return sel, en

    def __add_config_reg(self):
        for _, (sb, mux) in self.sb_muxs.items():
            config_name, en = self.get_mux_sel_name(sb)
            if mux.height > 1:
                self.add_config(config_name, mux.sel.width)
                self.wire(self.registers[config_name], mux.sel)

                self.add_config(en, mux.en.width)
                self.wire(self.registers[en], mux.en)

        for _, (reg_mux, mux) in self.reg_muxs.items():
            config_name, en = self.get_mux_sel_name(reg_mux)
            assert mux.height == 2
            self.add_config(config_name, mux.sel.width)
            self.wire(self.registers[config_name], mux.sel)

            self.add_config(en, mux.en.width)
            self.wire(self.registers[en], mux.en)

    def __handle_reg_clk_en(self):
        reg: FIFO
        for (reg_node, reg) in self.regs.values():
            rmux: RegisterMuxNode = list(reg_node)[0]
            # get rmux address
            config_name, _ = self.get_mux_sel_name(rmux)
            config_reg = self.registers[config_name]
            index_val = rmux.get_conn_in().index(reg_node)
            en = self.var(create_name(str(rmux)) + "_clk_en", 1)
            self.wire(en, (config_reg == index_val) & self.clk_en)
            self.wire(reg.clk_en, kratos.clock_en(en))


class TileCircuit(ReadyValidGenerator):
    def __init__(self, tiles: Dict[int, Tile], config_addr_width: int, config_data_width: int,
                 tile_id_width: int = 16,
                 full_config_addr_width: int = 32, debug: bool = False):
        x = -1
        y = -1
        core: Union[None, Core] = None
        self.additional_cores = []
        additional_core_names = set()
        for bit_width, tile in tiles.items():
            assert bit_width == tile.track_width
            if x == -1:
                x = tile.x
                y = tile.y
                assert isinstance(tile.core, Core)
                core = tile.core
            else:
                assert x == tile.x
                assert y == tile.y
                # the restriction is that all the tiles in the same coordinate
                # have to have the same core, otherwise it's physically
                # impossible
                assert core == tile.core
            for a_core, _ in tile.additional_cores:
                a_core = a_core
                assert isinstance(a_core, Core)
                core_name = a_core.name()
                if core_name not in additional_core_names:
                    self.additional_cores.append(a_core)
                    additional_core_names.add(core_name)

        assert x != -1 and y != -1
        self.x = x
        self.y = y
        self.core = core

        if self.core is None:
            name = "Tile_Empty"
        else:
            name = f"Tile_{self.core.core_name()}"
        super(TileCircuit, self).__init__(name, debug=debug)

        self.tiles = tiles
        self.config_addr_width = config_addr_width
        self.config_data_width = config_data_width
        self.tile_id_width = tile_id_width

        self.clk = self.clock("clk")
        # reset low
        self.reset = self.reset("rst_n", active_high=False)
        self.config_addr = self.input("config_addr", full_config_addr_width)
        self.config_data = self.input("config_data", config_data_width)

        # compute config addr sizes
        # (16, 24)
        full_width = full_config_addr_width
        self.full_config_addr_width = full_config_addr_width
        self.feature_addr_slice = slice(full_width - self.tile_id_width,
                                        full_width - self.config_addr_width)
        self.feature_addr_size = self.feature_addr_slice.stop - self.feature_addr_slice.start
        # (0, 16)
        self.tile_id_slice = slice(0, self.tile_id_width)
        # (24, 32)
        self.feature_config_slice = slice(full_width - self.config_addr_width,
                                          full_width)

        self.tile_id: kratos.Port
        self.tile_en: kratos.Var

        # create cb and switchbox
        self.cbs: Dict[str, CB] = {}
        self.sbs: Dict[int, SB] = {}

        self.features: List[Configurable] = []

        self.__create_cb()
        self.__create_sb()
        self.__lift_ports()
        self.__lift_internal_ports()

        self.__wire_cb()
        self.__connect_cb_sb()
        self.__connect_core()
        self.__setup_tile_id()

    def __create_cb(self):
        for bit_width, tile in self.tiles.items():
            # connection box time
            for port_name, port_node in tile.ports.items():
                # input ports
                if len(port_node) == 0:
                    assert bit_width == port_node.width
                    # make sure that it has at least one connection
                    if len(port_node.get_conn_in()) == 0:
                        continue
                    # create a CB
                    cb = CB(port_node, self.feature_addr_size, self.config_data_width, debug=self.debug)
                    self.add_child(f"CB_{port_name}", cb, clk=self.clk, rst_n=self.reset, config_data=self.config_data)
                    self.features.append(cb)
                else:
                    # output ports
                    assert len(port_node.get_conn_in()) == 0
                    assert bit_width == port_node.width

    def __create_sb(self):
        for bit_width, tile in self.tiles.items():
            core_name = self.core.name() if self.core is not None else ""
            sb = SB(tile.switchbox, self.feature_addr_size, self.config_data_width,
                    core_name, debug=self.debug)
            self.add_child(sb.name, sb, clk=self.clk, rst_n=self.reset)
            self.sbs[sb.switchbox.width] = sb

    def __wire_cb(self):
        for port_name, cb in self.cbs.items():
            p = self.__get_core_port(port_name)
            self.wire(cb.out_, p)
            valid_name = f"{port_name}_valid"
            valid = self.__get_core_port(valid_name)
            self.wire(cb.valid_out, valid)
            ready_name = f"{port_name}_ready"
            self.wire(cb.ready_in, self.__get_core_port(ready_name))

    def __connect_cb_sb(self):
        # connect ports from cb to switch box and back
        for _, cb in self.cbs.items():
            conn_ins = cb.node.get_conn_in()
            for idx, node in enumerate(conn_ins):
                assert isinstance(node,
                                  (SwitchBoxNode, RegisterMuxNode, PortNode))
                # for IO tiles they have connections to other tiles
                if node.x != self.x or node.y != self.y:
                    continue
                bit_width = node.width
                sb_circuit = self.sbs[bit_width]
                if not isinstance(node, PortNode):
                    # get the internal wire
                    n, sb_mux = sb_circuit.sb_muxs[str(node)]
                    assert n == node
                    sb_name = create_name(str(node))
                    if node.io == SwitchBoxIO.SB_IN:
                        self.wire(self.ports[sb_name], cb.in_[idx])
                        port_name = create_name(str(node)) + "_valid"
                        self.wire(self.ports[port_name],
                                  cb.ports.valid_in[idx])
                    else:
                        self.wire(sb_circuit.ports[sb_name], cb.in_[idx])
                else:
                    # this is an additional core port
                    # just connect directly
                    self.wire(self.__get_core_port(node.name), cb.in_[idx])
                    node_valid = node.name + "_valid"
                    p = self.__get_core_port(node_valid)
                    self.wire(p, cb.valid_in[idx])

    def __connect_core(self):
        for bit_width, tile in self.tiles.items():
            sb_circuit = self.sbs[bit_width]
            for _, port_node in tile.ports.items():
                if len(port_node) == 0:
                    continue
                assert len(port_node.get_conn_in()) == 0
                port_name = port_node.name
                for sb_node in port_node:
                    assert isinstance(sb_node, (SwitchBoxNode, PortNode))
                    if isinstance(sb_node, PortNode):
                        continue
                    # for IO tiles they have connections to other tiles
                    if sb_node.x != self.x or sb_node.y != self.y:
                        continue
                    idx = sb_node.get_conn_in().index(port_node)
                    # we need to find the actual mux
                    n, mux = sb_circuit.sb_muxs[str(sb_node)]
                    assert n == sb_node
                    self.wire(self.__get_core_port(port_name),
                              sb_circuit.ports[port_name])
                    sb_circuit.wire(sb_circuit.ports[port_name],
                                    mux.in_[idx])

                    ready_name = f"{port_name}_ready"
                    valid_name = f"{port_name}_valid"
                    loopback = self.var(f"{port_name}_valid_loopback", 1)
                    self.wire(loopback, sb_circuit.ports[ready_name] & self.__get_core_port(valid_name))
                    self.wire(sb_circuit.ports[valid_name], loopback)

    def __lift_ports(self):
        for _, switchbox in self.sbs.items():
            sbs = switchbox.switchbox.get_all_sbs()
            assert switchbox.switchbox.x == self.x
            assert switchbox.switchbox.y == self.y
            for sb in sbs:
                sb_name = create_name(str(sb))
                node, mux = switchbox.sb_muxs[str(sb)]
                assert node == sb
                assert sb.x == self.x
                assert sb.y == self.y
                port: _kratos.Port = switchbox.ports[sb_name]
                self.lift_rv(port)

    def __lift_internal_ports(self):
        for bit_width, sb in self.sbs.items():
            if sb.switchbox.num_track > 0:
                continue
            # lift the input ports up
            for port in self.core.inputs():
                if port.width != bit_width:
                    continue
                # depends on if the port has any connection or not
                # we lift the port up first
                # if it has no connection, then we lift it up
                port_name = port.name
                port_node = self.tiles[bit_width].ports[port.name]
                if port_node.get_conn_in():
                    cb_input_port = self.cbs[port_name].in_
                    self.lift_rv(cb_input_port)
                else:
                    p = self.core.ports[port_name]
                    self.lift_rv(p)

            # lift the output ports up
            for port in self.core.outputs():
                if port.width != bit_width:
                    continue
                port_name = port.name
                port_node = self.tiles[bit_width].ports[port_name]
                # depends on if the port has any connection or not
                # we lift the port up first
                # if it has connection, then we connect it to the core

                core_ready = self.core.ports[port_name + "_ready"]
                core_valid = self.core.ports[port_name + "_valid"]
                if len(port_node) > 1:
                    # and them together
                    ready_merge = self.var(core_ready + "_merge", 1)
                    ready = self.input(port_name + "_ready", len(port_node))
                    self.wire(ready_merge, ready.r_or())
                    self.wire(ready_merge, core_ready)
                    valid = self.input(port_name + "_valid", 1)
                    self.wire(valid, core_valid)
                    self.lift(port)
                else:
                    self.lift_rv(port)

    def __setup_tile_id(self):
        # tile id is set up as an external port to avoid unq in synthesis
        self.tile_id = self.input("tile_id", self.tile_id_width)
        self.tile_en = self.var("tile_en", 1)
        en = self.config_addr[self.tile_id_slice.stop - 1, self.tile_id_slice.start] == self.tile_id
        self.wire(self.tile_en, en)

    def finalize(self):
        for feat in self.features:
            feat.finalize()
        # set up config addr
        for feat_addr, feat in enumerate(self.features):
            en = self.var(feat.instance_name + "_en", 1)
            self.wire(en, self.config_addr[self.feature_config_slice.stop - 1,
                                           self.feature_config_slice.start].eq(feat_addr).eq(self.tile_en))
            self.wire(en, feat.config_en)
            self.wire(feat.config_addr,
                      self.config_addr[self.feature_addr_slice.stop - 1, self.feature_addr_slice.start])
            self.wire(feat.config_data, self.config_data)

    def __get_core_port(self, port_name):
        if port_name in self.core.ports:
            return self.core.ports[port_name]
        for core in self.additional_cores:
            if port_name in core.ports:
                return core.ports[port_name]
        return None


if __name__ == "__main__":
    def main():
        import kratos
        kratos.set_global_debug(True)
        switchbox = ImranSwitchBox(0, 0, 2, 1)
        sb = SB(switchbox, 8, 32, "Test")
        sb.finalize()
        kratos.verilog(sb, filename="test.sv")


    main()
