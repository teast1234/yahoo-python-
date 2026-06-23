# ============================================================
# models.py —— 数据传输对象（DTO）定义
# ------------------------------------------------------------
# Java 类比：相当于 Spring Boot 项目里的 DTO / VO / POJO 类。
# 在 Python 中我们用 Pydantic 这个库来声明数据模型，
# 它会自动帮我们做：字段校验、类型转换、JSON 序列化/反序列化。
# 类似 Java 中 Lombok @Data + Jackson @JsonProperty + javax.validation 的合体。
# ============================================================

from typing import Any  # Any 相当于 Java 的 Object，表示任意类型

# 从 pydantic 库导入两个核心工具：
#   BaseModel —— 所有数据模型的基类（类似 Java 中你自定义 DTO 时继承的某个父类）
#   Field     —— 用于给字段添加额外元信息（类似 Java 注解 @JsonProperty / @NotNull）
from pydantic import BaseModel, Field


class NewsArticle(BaseModel):
    """
    单条新闻文章的数据模型。
    Java 类比：相当于 public class NewsArticle { ... } 这样的 DTO。

    Python 中类的字段直接写在类体里，格式是：
        字段名: 类型 = 默认值
    冒号后面是类型注解（类似 Java 的字段类型声明）。
    `str | None` 表示「字符串 或者 null」，等价于 Java 中允许为 null 的 String。
    """

    # 新闻 id，可空。等同于 Java：private String id;
    id: str | None = None

    # 新闻标题
    title: str | None = None

    # 摘要 / 简介
    summary: str | None = None

    # 发布机构（如 Reuters、Bloomberg）
    publisher: str | None = None

    # 新闻原文链接
    link: str | None = None

    # 发布时间
    # Field(alias="pubDate") 类似 Java 中的 @JsonProperty("pubDate")
    # 作用：Python 内部使用 pub_date（蛇形命名，符合 PEP 8），
    #       但与外部 JSON 交互时使用 pubDate（驼峰命名，符合 Yahoo 接口风格）。
    pub_date: str | None = Field(default=None, alias="pubDate")

    # 内容类型，例如 STORY / VIDEO 等
    type: str | None = None

    # 文章正文（按需填充）。Yahoo 列表接口默认不返回正文，
    # 需要再次请求文章详情页（link）抓取并解析后写回这里。
    content: str | None = None

    # 原始数据（保留 Yahoo 返回的完整 JSON），dict 相当于 Java 的 Map<String, Object>
    raw: dict[str, Any] | None = None

    # ---------- Pydantic 配置 ----------
    # model_config 是 Pydantic v2 的配置写法。
    # populate_by_name=True 表示：既支持用字段名 pub_date 赋值，
    # 也支持用别名 pubDate 赋值（双向兼容）。
    # 类似 Jackson 的 @JsonAlias 效果。
    model_config = {"populate_by_name": True}


class NewsResponse(BaseModel):
    """
    HTTP 接口的响应包装类。
    Java 类比：相当于一个 ResponseVO / ApiResult<List<NewsArticle>> 之类的包装对象。
    """

    ticker: str          # 股票代码，例如 "AAPL"。相当于 Java：private String ticker;
    tab: str             # 新闻类型 tab：news / all / press releases
    count: int           # 实际返回的新闻条数
    articles: list[NewsArticle]  # 新闻列表，相当于 Java：private List<NewsArticle> articles;
