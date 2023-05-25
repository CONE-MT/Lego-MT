import json

import pandas as pd
from xpinyin import Pinyin
import re


from cm2m_utils.english_centered_family_info import X_X_FAMILY_8
p = Pinyin()

top_lgs = ["en", "zh", "fr", "de"]
target_lgs = []
for family, lgs in X_X_FAMILY_8.items():
    num = int(re.findall("\d+",family)[0])
    if num < 7:
        target_lgs.extend(lgs)
        for lg in lgs:
            if lg not in top_lgs:
                target_lgs.append(lg)
print(",".join(target_lgs))

df = pd.read_excel("./langid2lang_chinese5.xlsx")
priority_list = []
rest_list = []
result_dict = {}
df["pinyin"] = df.apply(lambda x: p.get_pinyin(x["语言中文"]).replace("-", ""), axis=1)
for i, row in df.iterrows():
    en_lg = row["语言英文"]
    lg = row["语言缩写"]
    zh_lg = row["语言中文"]
    pinyin = row["pinyin"]
    row = {
        "en": en_lg,
        "abs": lg,
        "chinese": zh_lg,
        "pinyin": pinyin
    }
    if lg in target_lgs:
        priority_list.append(row)
    else:
        rest_list.append(row)

priority_list.extend(rest_list)
result_dict[lg] = row

with open("data.js", "w", encoding="utf-8") as writer:
    writer.write("const languageTable = [ \n")
    for tmp_list in priority_list:
        writer.write(json.dumps(tmp_list))
    for lg in top_lgs:
        writer.write(json.dumps(result_dict[lg]))
        writer.write(",\n")
    for lg in target_lgs:
        writer.write(json.dumps(result_dict[lg]))
        writer.write(",\n")
    for lg, row in result_dict.items():
        if lg not in top_lgs and lg not in target_lgs:
            writer.write(json.dumps(row))
            writer.write(",\n")
    writer.write("];\nexport { languageTable };")


