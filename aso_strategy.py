import zipfile
z=zipfile.ZipFile('/mnt/data/aso-news-bot-main.zip')
print(z.read('aso-news-bot-main/aso_strategy.py').decode('utf-8'))