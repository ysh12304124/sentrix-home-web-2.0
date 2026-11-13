"""跨层共享的运行时上下文。

这里刻意不依赖任何一层：application 需要给导入步骤打标记，infrastructure 需要读
这个标记附加请求头，两侧都不能互相 import。
"""
