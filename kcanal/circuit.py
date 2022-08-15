import kratos

from typing import List, Dict, Tuple
from .cyclone import InterconnectCore, PortNode, Node, SwitchBox, RegisterNode, RegisterMuxNode, SwitchBoxNode, \
    SwitchBoxIO
from .logic import Configurable, Mux, FIFO


class Core(kratos.Generator, InterconnectCore):
    def __init__(self, name: str, debug: bool = False):
        super(Core, self).__init__(name, debug)

        self.__input_ports: List[kratos.Port] = []
        self.__output_ports: List[kratos.Port] = []

    def input_rv(self, name, width) -> kratos.Port:
        p = self.input(name, width)
        # also add ready valid interface
        self.input(f"{name}_valid", 1)
        self.output(f"{name}_ready", 1)
        self.__input_ports.append(p)
        return p

    def output_rv(self, name, width) -> kratos.Port:
        p = self.output(name, width)
        # also add ready valid interface
        self.output(f"{name}_valid", 1)
        self.input(f"{name}_ready", 1)
        self.__output_ports.append(p)
        return p

    def inputs(self) -> List[kratos.Port]:
        return self.__input_ports

    def outputs(self) -> List[kratos.Port]:
        return self.__output_ports


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
    assert len(conn_in) > 0, f"{node} does not have any connections"
    height = len(conn_in)
    mux = Mux(height, node.width)
    return mux


class CB(Configurable):
    def __init__(self, node: PortNode,
                 config_addr_width: int, config_data_width: int):
        self.node = node
        self.width = node.width
        super(CB, self).__init__(create_name(str(node)), config_addr_width, config_data_width)

        self.mux = _create_mux(node)
        self.in_ = self.input("I", self.width, size=[self.mux.height], explicit_array=True)
        self.out_ = self.output("O", self.width)
        self.sel = self.add_config("sel", self.mux.sel.width)
        self.valid_in = self.port_from_def(self.mux.valid_in)
        self.valid_out = self.port_from_def(self.mux.valid_out)
        self.ready_in = self.port_from_def(self.mux.ready_in)
        self.ready_out = self.port_from_def(self.mux.ready_out)

        self.add_child("mux", self.mux,
                       I=self.in_, O=self.out_, S=self.sel, valid_in=self.valid_in, valid_out=self.valid_out,
                       ready_in=self.ready_in, ready_out=self.ready_out)


class SB(Configurable):
    def __init__(self, switchbox: SwitchBox, config_addr_width: int, config_data_width: int, core_name: str):
        name = f"SB_ID{switchbox.id}_{switchbox.num_track}TRACKS_B{switchbox.width}_{core_name}"
        super(SB, self).__init__(name, config_addr_width, config_data_width)
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

        self.__add_config_reg()
        self.__handle_reg_clk_en()

        self.__lift_ports()

    def __create_sb_mux(self):
        sbs = self.switchbox.get_all_sbs()
        for sb in sbs:
            sb_name = str(sb)
            self.sb_muxs[sb_name] = (sb, _create_mux(sb))

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
            if sb.io == SwitchBoxIO.SB_IN:
                p = self.port_from_def(mux.in_, port_name)
                self.wire(p, mux.out_)
            else:
                p = self.port_from_def(mux.out_, port_name)

                # to see if we have a register mux here
                # if so , we need to lift the reg_mux output instead
                if sb_name in self.reg_muxs:
                    # override the mux value
                    node, mux = self.reg_muxs[sb_name]
                    assert isinstance(node, RegisterMuxNode)
                    assert node in sb
                self.wire(p, mux.out_)

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
            self.__handle_rmux_fanin(sb_node, n, node)

    @staticmethod
    def __get_mux_sel_name(node: Node):
        return f"{create_name(str(node))}_sel"

    def __add_config_reg(self):
        for _, (sb, mux) in self.sb_muxs.items():
            config_name = self.__get_mux_sel_name(sb)
            if mux.height > 1:
                self.add_config(config_name, mux.sel.width)
                self.wire(self.registers[config_name], mux.sel)

        for _, (reg_mux, mux) in self.reg_muxs.items():
            config_name = self.__get_mux_sel_name(reg_mux)
            assert mux.height == 2
            self.add_config(config_name, mux.sel.width)
            self.wire(self.registers[config_name], mux.sel)

    def __handle_rmux_fanin(self, sb: Node, rmux: RegisterMuxNode,
                            reg: RegisterNode):
        pass

    def __handle_reg_clk_en(self):
        reg: FIFO
        for (reg_node, reg) in self.regs.values():
            rmux: RegisterMuxNode = list(reg_node)[0]
            # get rmux address
            config_name = self.__get_mux_sel_name(rmux)
            config_reg = self.registers[config_name]
            index_val = rmux.get_conn_in().index(reg_node)
            en = self.var(create_name(str(rmux)) + "_clk_en", 1)
            self.wire(en, (config_reg == index_val) & self.clk_en)
            self.wire(reg.clk_en, kratos.clock_en(en))


if __name__ == "__main__":
    def main():
        import kratos
        node = PortNode("test", 0, 0, 16)
        for _ in range(5):
            Node(0, 0, 16).add_edge(node)

        cb = CB(node, 32, 32)
        cb.finalize()
        kratos.verilog(cb, filename="test.sv")


    main()
