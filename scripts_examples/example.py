import sys
from datetime import datetime

args = sys.argv[1:]

print(f'Время: {datetime.now().isoformat()}.\nАргументы: {" ".join(args)}')

print('Демонстрация завершена.')
