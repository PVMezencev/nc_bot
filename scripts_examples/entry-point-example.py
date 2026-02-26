import sys
import time
from datetime import datetime

args = sys.argv[1:]

counter = 0
while counter < 10:
    print(f'Шаг: {counter}. Время: {datetime.now().isoformat()}.\nАргументы: {" ".join(args)}')

    time.sleep(1)
    counter += 1

print('Демонстрация завершена.')
