import unittest
import json
from backend.app.services.resume_title import resume_title

class ResumeTitleTests(unittest.TestCase):
    def test_titles(self):
        for mode, identity, expected in json.loads('[\n  ["paper", {"year":2016,"paper":"2016年考研英语一真题","unit":"阅读理解 Text 1"}, "2016 年 · 考研英语一真题"],\n  ["random", {"year":2016,"paper":"2016年考研英语一真题","unit":"2016 年 · 阅读理解 Text 1"}, "2016 年 · 考研英语一真题 · 阅读理解 Text 1"],\n  ["random", {"year":2016,"paper":"2016年考研英语二真题","unit":"阅读理解 Text 1"}, "2016 年 · 考研英语二真题 · 阅读理解 Text 1"],\n  ["paper", {"year":2016,"paper":"练习12016套"}, "2016 年 · 练习12016套"],\n  ["paper", {"year":2016,"paper":"真题"}, "2016 年 · 真题"],\n  ["paper", {"year":2016,"paper":"2016年真题 2016年"}, "2016 年 · 真题"],\n  ["random", {"paper":"模拟套卷A","unit":"Text 2"}, "模拟套卷A · Text 2"]\n]'):
            with self.subTest(identity=identity):
                self.assertEqual(resume_title(mode, identity), expected)
