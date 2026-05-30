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

test_cases = [
    ("Hello World", "𝖧𝖾𝗅𝗅𝗈 𝖶𝗈𝗋𝗅𝖽"),
    ("<b>Bold Text</b>", "<b>𝖡𝗈𝗅𝖽 𝖳𝖾𝗑𝗍</b>"),
    ("<code>Code Block</code>", "<code>Code Block</code>"),
    ("Hello {name}, your size is {Size}", "𝖧𝖾𝗅𝗅𝗈 {name}, 𝗒𝗈𝗎𝗋 𝗌𝗂𝗓𝖾 𝗂𝗌 {Size}"),
    ("Check https://example.com/page", "𝖢𝗁𝖾𝖼𝗄 https://example.com/page"),
    ("Contact @username or use /start", "𝖢𝗈𝗇𝗍𝖺𝖼𝗍 @username 𝗈𝗋 𝗎𝗌𝖾 /start"),
    ("Mixed <i>Italic</i> and <code>Code</code> with {Var}", "𝖬𝗂𝗑𝖾𝖽 <i>𝖨𝗍𝖺𝗅𝗂𝖼</i> 𝖺𝗇𝖽 <code>Code</code> 𝗐𝗂𝗍𝗁 {Var}"),
    ("Entities: &lt; &amp; &quot; &#123;", "𝖤𝗇𝗍𝗂𝗍𝗂𝖾𝗌: &lt; &amp; &quot; &#123;"),
    ("URL with punctuation: https://google.com. Please visit.", "𝖴𝖱𝖫 𝗐𝗂𝗍𝗁 𝗉𝗎𝗇𝖼𝗍𝗎𝖺𝗍𝗂𝗈𝗇: https://google.com. 𝖯𝗅𝖾𝖺𝗌𝖾 𝗏𝗂𝗌𝗂𝗍.")
]

for input_text, expected_output in test_cases:
    actual_output = convert_to_tian(input_text)
    print(f"Input:    {input_text}")
    print(f"Expected: {expected_output}")
    print(f"Actual:   {actual_output}")
    assert actual_output == expected_output
    print("MATCHED!")
    print("-" * 20)

print("All test cases passed!")
