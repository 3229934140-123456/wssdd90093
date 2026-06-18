class AnalysisError(Exception):
    def __init__(self, message: str, code: str = "ANALYSIS_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)


class ContentHashError(AnalysisError):
    def __init__(self, message: str = "无法生成内容哈希"):
        super().__init__(message, "CONTENT_HASH_ERROR")


class DatabaseError(AnalysisError):
    def __init__(self, message: str = "数据库操作失败"):
        super().__init__(message, "DATABASE_ERROR")
