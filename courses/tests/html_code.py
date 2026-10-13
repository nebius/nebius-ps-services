"""Read displayed code independently of the builder's token markup."""

from html.parser import HTMLParser


class CodeBlocks(HTMLParser):
    def __init__(self, document: str):
        super().__init__()
        self.blocks: list[dict] = []
        self.in_pre = False
        self.in_code = False
        self.in_comment = False
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "pre":
            self.in_pre = True
        elif tag == "code" and self.in_pre:
            self.in_code = True
            self.blocks.append(dict(attrs=attrs, text="", comments=[]))
        elif tag == "span" and self.in_code:
            assert attrs == {"class": "code-comment"}
            self.in_comment = True
            self.blocks[-1]["comments"].append("")

    def handle_data(self, text):
        if self.in_code:
            self.blocks[-1]["text"] += text
            if self.in_comment:
                self.blocks[-1]["comments"][-1] += text

    def handle_endtag(self, tag):
        if tag == "span":
            self.in_comment = False
        elif tag == "code":
            self.in_code = False
        elif tag == "pre":
            self.in_pre = False


def code_blocks(document: str) -> list[str]:
    return [block["text"] for block in CodeBlocks(document).blocks]
