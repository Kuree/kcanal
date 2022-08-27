import tempfile
import os


def test_dump_pnr(create_dummy_interconnect):
    interconnect = create_dummy_interconnect(4, 4)

    design_name = "test"
    with tempfile.TemporaryDirectory() as tempdir:
        interconnect.dump_pnr(tempdir, design_name)

        assert os.path.isfile(os.path.join(tempdir, f"{design_name}.info"))
        assert os.path.isfile(os.path.join(tempdir, "1.graph"))
        assert os.path.isfile(os.path.join(tempdir, "16.graph"))
        assert os.path.isfile(os.path.join(tempdir, f"{design_name}.layout"))


if __name__ == "__main__":
    from conftest import create_dummy_interconnect_fn
    test_dump_pnr(create_dummy_interconnect_fn)
