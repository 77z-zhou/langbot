from rich.console import Console

console = Console()

class LangBot:
    def __init__(self, enabled=True):
        self._spinner = console.status(
            "[dim]langbot is thinking...[/dim]", spinner="dots"
        ) if enabled else None

    def think(self):
        if self._spinner:
            with self._spinner:  # 启动 spinner
                # 模拟耗时操作
                import time
                time.sleep(3)
                result = "Here is your answer!"
        else:
            # 不使用 spinner 时直接执行
            import time
            time.sleep(3)
            result = "Here is your answer!"
        return result

bot = LangBot(enabled=True)
print(bot.think())