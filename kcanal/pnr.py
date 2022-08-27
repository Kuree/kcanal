class PnRTag:
    DEFAULT_PRIORITY = 20

    def __init__(self, tag_name: str, priority_major: int,
                 priority_minor: int):
        assert len(tag_name) == 1, "Tag can only be one character"
        self.tag_name = tag_name
        self.priority_major = priority_major
        self.priority_minor = priority_minor

    def __eq__(self, other):
        if not isinstance(other, PnRTag):
            return False
        return self.tag_name == other.tag_name \
            and self.priority_major == self.priority_major \
            and self.priority_minor == self.priority_minor

    def __hash__(self):
        return hash(self.tag_name)