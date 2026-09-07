# 芯片设计入门智能体

给行业小白的在线手册：完整流程、模块作用、协议体系、思维导图、自测题，以及可引用教材的导学智能体。

## 启动

```bash
cd d:\pythonSpace\ChipDesignAgent
uv sync
uv run python main.py
```

浏览器打开 http://127.0.0.1:8765

复制 `.env.example` 为 `.env` 并填入 OpenAI 兼容接口后，智能体可讲解与出题；不填则自动降级为教材检索。

## 内容结构

- `content/curriculum.yaml`：8 篇 48 章大纲
- `content/parts/`：入门正文
- `content/mindmaps/`：全书与分篇导图
- `content/quizzes/`：章测 + 综合测验，共 232 题
