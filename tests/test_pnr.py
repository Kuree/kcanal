import tempfile
import os
import archipelago

from kcanal.cyclone import PortNode


def test_dump_pnr(create_dummy_interconnect):
    interconnect = create_dummy_interconnect(4, 4)

    design_name = "test"
    with tempfile.TemporaryDirectory() as tempdir:
        interconnect.dump_pnr(tempdir, design_name)

        assert os.path.isfile(os.path.join(tempdir, f"{design_name}.info"))
        assert os.path.isfile(os.path.join(tempdir, "1.graph"))
        assert os.path.isfile(os.path.join(tempdir, "16.graph"))
        assert os.path.isfile(os.path.join(tempdir, f"{design_name}.layout"))


def test_pnr(create_dummy_interconnect):
    interconnect = create_dummy_interconnect(4, 4)
    netlist = {"e0": [("D0", "out16"), ["D1", "in16"]]}
    net_width = {"e0": 16}
    input_netlist = (netlist, net_width)

    with tempfile.TemporaryDirectory() as tempdir:
        placement, routing, id_to_name = archipelago.pnr(interconnect, input_netlist, cwd=tempdir)
        assert "D0" in placement
        assert "D1" in placement
        assert "e0" in routing
        assert isinstance(routing["e0"][0][0], PortNode)


if __name__ == "__main__":
    from conftest import create_dummy_interconnect_fn
    test_pnr(create_dummy_interconnect_fn)
