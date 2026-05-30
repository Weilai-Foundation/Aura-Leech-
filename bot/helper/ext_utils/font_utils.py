import re

def convert_to_tian(text):
    if not text or not isinstance(text, str):
        return text

    def replace_char(c):
        o = ord(c)
        if 65 <= o <= 90: # A-Z
            return chr(o + 120159)
        elif 97 <= o <= 122: # a-z
            return chr(o + 120153)
        return c

    # Pattern to match things to SKIP
    # 1. Code blocks: <code>.*?</code>, <pre>.*?</pre>
    # 2. HTML tags: <[^>]+>
    # 3. HTML entities: &[a-zA-Z0-9#]+;
    # 4. Placeholders: \{[^{}]+\}
    # 5. URLs: https?://[^\s<>"]+
    # 6. Mentions: @[a-zA-Z0-9_]+
    # 7. Commands: /[a-zA-Z0-9_]+
    pattern = r'(<code>.*?</code>|<pre>.*?</pre>|<[^>]+>|&[a-zA-Z0-9#]+;|\{[^{}]+\}|https?://[^\s<>"]+|@[a-zA-Z0-9_]+|/[a-zA-Z0-9_]+)'

    parts = re.split(pattern, text, flags=re.DOTALL)

    for i in range(len(parts)):
        if i % 2 == 0: # This is text to convert
            parts[i] = "".join(replace_char(c) for c in parts[i])

    return "".join(parts)
