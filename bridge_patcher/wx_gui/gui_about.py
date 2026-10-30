"""
gui_about.py: About frame
"""

import wx
import markdown
import re
import logging
from pathlib import Path
from .. import constants



class AboutFrame(wx.Frame):

    def __init__(self, global_constants: constants.Constants) -> None:
        if wx.FindWindowByName("About"):
            return

        logging.info("Generating About frame")
        super(AboutFrame, self).__init__(None, title="About", size=(500, 600), style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX))
        self.constants: constants.Constants = global_constants
        self.Centre()
        self.hyperlink_colour = (25, 179, 231)

        self._generate_elements(self)

        self.Show()


    def _generate_elements(self, frame: wx.Frame) -> None:

        self.webview = wx.html2.WebView.New(self)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.webview, 1, wx.EXPAND)
        self.SetSizer(sizer)

        self.load_markdown()

    def load_markdown(self):
        markdown_text = Path("./README.md").read_text(encoding="utf-8")

        # Call your custom function here.
        html_body = self.render_markdown(markdown_text)

        html_document = f"""<!doctype html>
<html>
<head>
    <meta charset="utf-8">

    <style>
        :root {{
            color-scheme: light;

            --background: #ffffff;
            --foreground: #202124;
            --muted-foreground: #5f6368;
            --link: #268bd2;
            --code-background: #f0f0f0;
            --border: #999999;
            --blockquote-border: #cccccc;
        }}

        html,
        body {{
            background: var(--background);
            color: var(--foreground);
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                         sans-serif;
            max-width: 900px;
            margin: 0 auto;
            padding: 24px;
            line-height: 1.5;
        }}

        img {{
            max-width: 100%;
            height: auto;
        }}

        input[type="checkbox"] {{
            width: 1.1em;
            height: 1.1em;
            vertical-align: middle;
            margin-right: 0.4em;
        }}

        .task-list-item {{
            list-style: none;
        }}

        ul:has(.task-list-item) {{
            padding-left: 0;
        }}

        pre {{
            overflow-x: auto;
            padding: 12px;
            border-radius: 6px;
            background: var(--code-background);
            color: var(--foreground);
        }}

        code {{
            background: var(--code-background);
            color: var(--foreground);
        }}

        table {{
            border-collapse: collapse;
        }}

        th,
        td {{
            border: 1px solid var(--border);
            padding: 6px 10px;
        }}

        blockquote {{
            margin-left: 0;
            padding-left: 16px;
            border-left: 4px solid var(--blockquote-border);
            color: var(--muted-foreground);
        }}

        a {{
            color: var(--link);
        }}

        @media (prefers-color-scheme: dark) {{
            :root {{
                color-scheme: dark;

                --background: #0d1117;
                --foreground: #e6edf3;
                --muted-foreground: #8b949e;
                --link: #58a6ff;
                --code-background: #161b22;
                --border: #30363d;
                --blockquote-border: #30363d;
            }}
        }}
    </style>
</head>
<body>
{html_body}
</body>
</html>
"""

        # This lets relative images and links resolve relative to the
        # directory containing the Markdown file.
        base_url = Path("./README.md").resolve().parent.as_uri() + "/"

        self.webview.SetPage(html_document, base_url)


    def render_markdown(self, text: str):
        # Convert bare GitHub-style task lines into Markdown list items.
        #
        # Input:
        #   [X] Installer boots
        #
        # Becomes:
        #   - [X] Installer boots
        text = re.sub(
            r"^(\s*)(\[[ xX]\])\s+(.*)$",
            r"\1- \2 \3",
            text,
            flags=re.MULTILINE,
        )

        html = markdown.markdown(
            text,
            extensions=[
                "extra",
                "tables",
                "fenced_code",
                "sane_lists",
            ],
        )

        # Convert [X] and [ ] inside <li> elements into HTML checkboxes.
        def checkbox_replacement(match):
            checked = match.group(1).lower() == "x"

            return (
                '<li class="task-list-item">'
                '<input type="checkbox" '
                f'{"checked " if checked else ""}'
                'disabled> '
            )

        html = re.sub(
            r"<li>\[([ xX])\]\s*",
            checkbox_replacement,
            html,
            flags=re.IGNORECASE,
        )

        return html