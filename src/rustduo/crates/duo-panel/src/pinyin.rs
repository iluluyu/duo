//! 拼音首字母排序键（apps.py pinyin_initial / label_sort_key 对译；GB2312
//! 编码经 encoding_rs GB18030 双字节序列取得）。合同镜像 test_apps.py。

use encoding_rs::GB18030;

/// GB2312 一级汉字区间的音序段尾（apps.py _PINYIN_RUN_ENDS，逐项对译）。
const PINYIN_RUN_ENDS: [(u16, &str); 23] = [
    (0xB0C4, "a"),
    (0xB2C0, "b"),
    (0xB4EE, "c"),
    (0xB6E9, "d"),
    (0xB7A1, "e"),
    (0xB8C0, "f"),
    (0xB9FD, "g"),
    (0xBBF6, "h"),
    (0xBFA5, "j"),
    (0xC0AB, "k"),
    (0xC2E7, "l"),
    (0xC4C2, "m"),
    (0xC5B6, "n"),
    (0xC5BD, "o"),
    (0xC6D9, "p"),
    (0xC8BA, "q"),
    (0xC8F5, "r"),
    (0xCBFA, "s"),
    (0xCDD9, "t"),
    (0xCEF3, "w"),
    (0xD1B9, "x"),
    (0xD4D0, "y"),
    (0xD7F9, "z"),
];

/// 音序表外常用 app 名汉字（apps.py _PINYIN_EXTRA，逐项对译）。
const PINYIN_EXTRA: [(&str, &str); 46] = [
    ("吧", "b"),
    ("哔", "b"),
    ("魑", "c"),
    ("琛", "c"),
    ("哒", "d"),
    ("咚", "d"),
    ("嗲", "d"),
    ("斐", "f"),
    ("嗨", "h"),
    ("浣", "h"),
    ("獾", "h"),
    ("珩", "h"),
    ("晗", "h"),
    ("咔", "k"),
    ("氪", "k"),
    ("铠", "k"),
    ("浏", "l"),
    ("翎", "l"),
    ("岚", "l"),
    ("嘞", "l"),
    ("魉", "l"),
    ("啰", "l"),
    ("咪", "m"),
    ("喵", "m"),
    ("魅", "m"),
    ("旻", "m"),
    ("妞", "n"),
    ("嗯", "n"),
    ("噗", "p"),
    ("貔", "p"),
    ("穹", "q"),
    ("蜻", "q"),
    ("嗖", "s"),
    ("蜓", "t"),
    ("钛", "t"),
    ("魍", "w"),
    ("枭", "x"),
    ("貅", "x"),
    ("玺", "x"),
    ("晞", "x"),
    ("曜", "y"),
    ("嬴", "y"),
    ("樾", "y"),
    ("昱", "y"),
    ("玥", "y"),
    ("崽", "z"),
];

fn gb2312_code(ch: char) -> Option<u16> {
    let single = ch.to_string();
    let (bytes, _, had_errors) = GB18030.encode(&single);
    if had_errors {
        return None;
    }
    match bytes.as_ref() {
        // GB2312 双字节区（首字节 0xA1-0xF7）；四字节序列不是 GB2312。
        [hi, lo] if (0xA1..=0xF7).contains(hi) => Some(u16::from(*hi) << 8 | u16::from(*lo)),
        _ => None,
    }
}

/// 单字符拼音首字母（小写）；拉丁/数字/假名/未收汉字原样小写。
pub fn pinyin_initial(ch: char) -> String {
    let mut single = String::new();
    single.push(ch);
    if let Some((_, initial)) = PINYIN_EXTRA
        .iter()
        .find(|(known, _)| *known == single.as_str())
    {
        return initial.to_string();
    }
    if let Some(code) = gb2312_code(ch) {
        if (0xB0A1..=0xD7F9).contains(&code) {
            if let Some((_, initial)) = PINYIN_RUN_ENDS.iter().find(|(end, _)| code <= *end) {
                return initial.to_string();
            }
        }
    }
    ch.to_lowercase().to_string()
}

/// 排序键："不背单词" → "bbdc"，与拉丁名同键空间。
pub fn label_sort_key(label: &str) -> String {
    label
        .chars()
        .map(pinyin_initial)
        .collect::<String>()
        .to_lowercase()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_table_classifies_common_hanzi() {
        assert_eq!(pinyin_initial('微'), "w");
        assert_eq!(pinyin_initial('信'), "x");
        assert_eq!(pinyin_initial('安'), "a");
        assert_eq!(pinyin_initial('卓'), "z");
        assert_eq!(pinyin_initial('一'), "y");
        assert_eq!(pinyin_initial('行'), "x");
    }

    #[test]
    fn extra_dict_covers_out_of_table() {
        assert_eq!(pinyin_initial('哔'), "b");
        assert_eq!(pinyin_initial('匙'), "c");
    }

    #[test]
    fn non_hanzi_passes_through_lowercased() {
        assert_eq!(pinyin_initial('W'), "w");
        assert_eq!(pinyin_initial('9'), "9");
    }

    #[test]
    fn label_sort_key_orders_launcher_style() {
        let mut labels = ["微信", "哔哩哔哩", "不背单词"];
        labels.sort_by_key(|l| label_sort_key(l));
        assert_eq!(labels, ["不背单词", "哔哩哔哩", "微信"]);
        assert_eq!(label_sort_key("WPS Office"), "wps office");
    }
}
