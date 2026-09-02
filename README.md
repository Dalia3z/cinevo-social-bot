# 🎬 Cinevo AI Social Media Engagement Bot

نظام أتمتة متكامل للرد التلقائي على التعليقات والتفاعلات عبر منصات متعددة
(Facebook, Instagram, YouTube Shorts, TikTok, Quora) باستخدام **DeepSeek AI**
لتوليد ردود قيّمة ومفيدة في حدود سطرين، مع دمج رابط موقع
[Cinevo](https://cinevoapp.com) بطريقة طبيعية واحترافية دون أي مظهر مزعج (Spam).

---

## ✨ المزايا

- **ردود ذكية وقيمة**: برومبت صارم يوجّه النموذج لإعطاء قيمة حقيقية (90%)
  مرتبطة بسياق التعليق، في **سطرين كحد أقصى**.
- **دمج طبيعي للرابط**: يُدرج رابط `https://cinevoapp.com` فقط عندما يلائم
  النقاش، وبصياغة طبيعية غير إعلانية.
- **منع التكرار نهائياً**: قاعدة بيانات SQLite محلية تخزّن كل `comment_id`
  / `post_id` الذي تم الرد عليه.
- **حماية من الحظر (Anti-Ban)**: فاصل زمني عشوائي (افتراضياً 3–7 دقائق) بين
  كل رد والآخر، مع احترام حدود الـ Rate Limits.
- **تشغيل مستمر**: يعمل في الخلفية عبر **PM2** أو **systemd** على الـ VPS.

---

## 📁 هيكل المشروع

```
cinevo-social-bot/
├── main.py                  # نقطة التشغيل + حلقة التحكم (Worker Loop)
├── config.py                # إدارة مفاتيح البيئة والتوكنات
├── database.py              # إدارة SQLite لمنع تكرار الردود
├── ai_handler.py            # تكامل DeepSeek وتوليد الردود
├── requirements.txt         # مكتبات بايثون المطلوبة
├── ecosystem.config.js      # تشغيل PM2
├── cinevo-bot.service       # (بديل) خدمة systemd
├── .env.example             # قالب متغيرات البيئة
├── platforms/
│   ├── __init__.py
│   ├── youtube.py           # YouTube Shorts (Data API v3)
│   ├── meta.py              # Facebook + Instagram (Graph API)
│   ├── tiktok.py            # TikTok
│   └── quora.py             # Quora
└── logs/                    # ملفات السجل
```

---

## 🚀 دليل التشغيل السريع (Linux VPS)

### 1) تحديث النظام وتثبيت الأدوات

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git curl
```

### 2) تثبيت Node.js و PM2 (للتشغيل المستمر)

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g pm2
```

### 3) رفع المشروع إلى السيرفر

```bash
# انسخ مجلد cinevo-social-bot إلى السيرفر، ثم:
cd cinevo-social-bot
```

### 4) إنشاء بيئة Python افتراضية وتثبيت المكتبات

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 5) إعداد ملف البيئة `.env`

```bash
cp .env.example .env
nano .env
```

املأ القيم الحقيقية (مفاتيح DeepSeek، YouTube، Meta...). راجع
[`.env.example`](.env.example) لشرح كل متغير.

### 6) اختبار التشغيل (بدون نشر فعلي)

```bash
# تشغيل دورة واحدة بدون نشر أي رد (للتأكد من سلامة الإعدادات)
python main.py --once --dry-run

# أو تشغيل دورة واحدة بشكل فعلي
python main.py --once
```

### 7) التشغيل المستمر عبر PM2

```bash
# عدّل ecosystem.config.js إن لزم (المسار، python3)
pm2 start ecosystem.config.js
pm2 save
pm2 startup   # اتبع التعليمات التي تظهر لتفعيل التشغيل عند الإقلاع
```

أوامر مفيدة:

```bash
pm2 logs cinevo-bot        # مشاهدة السجلات
pm2 status                 # حالة العملية
pm2 restart cinevo-bot     # إعادة التشغيل بعد تعديل الكود
pm2 stop cinevo-bot        # إيقاف البوت
```

> **بديل systemd**: انسخ [`cinevo-bot.service`](cinevo-bot.service) إلى
> `/etc/systemd/system/`، عدّل المسارات، ثم:
> `sudo systemctl enable --now cinevo-bot`

---

## ⚙️ الإعداد لكل منصة

### YouTube Shorts
- احصل على **API key** من Google Cloud Console (لقراءة التعليقات).
- للنشر (الرد) تحتاج **OAuth 2.0 token** بصلاحية `youtube.force-ssl`.
- ضع معرفات الفيديوهات في `YOUTUBE_VIDEO_IDS`.

### Facebook & Instagram (Meta Graph API)
- أنشئ تطبيق Meta وأنشئ **Page access token** طويل الأمد.
- الصلاحيات المطلوبة: `pages_manage_posts`, `pages_read_engagement`,
  `instagram_basic`, `instagram_manage_comments`.
- ضع معرفات الصفحات وحسابات Instagram في `.env`.

### TikTok
- **ملاحظة مهمة**: واجهة TikTok العامة لقراءة/الرد على التعليقات غير متاحة
  حالياً إلا لتطبيقات معتمدة. اترك `TIKTOK_ENABLED=false` ما لم يكن تطبيقك
  معتمداً. الوحدة جاهزة للربط عند توفر النقاط الرسمية.

### Quora
- **لا توجد واجهة رسمية** لـ Quora. الوضع الافتراضي يولّد إجابات ويخزّنها
  في مجلد `quora_queue/` للمراجعة اليدوية (آمن 100%).
- أتمتة المتصفح (`QUORA_BROWSER_AUTOMATION=true`) عالية الخطورة وتتطلب
  `selenium`، استخدمها على مسؤوليتك.

---

## 🛡️ الحماية من الحظر (Anti-Ban)

- `MIN_DELAY_SECONDS` / `MAX_DELAY_SECONDS`: فاصل عشوائي بين الردود
  (افتراضياً 180–420 ثانية = 3–7 دقائق).
- `POLL_INTERVAL_SECONDS`: فترة جلب التعليقات الجديدة.
- `MAX_COMMENTS_PER_CYCLE`: حد أقصى للتعليقات لكل دورة.
- قاعدة بيانات SQLite تمنع إعادة الرد على نفس العنصر.

---

## 🧠 كيف يعمل توليد الرد (DeepSeek)

يستخدم [`ai_handler.py`](ai_handler.py) برومبت نظام صارم يضمن:
1. **قيمة أولاً**: 90% من الرد إجابة/معلومة مفيدة مرتبطة بالسياق.
2. **سطران كحد أقصى**: مباشر وجذاب ومختصر.
3. **الرابط فقط عند الملاءمة**: يُذكر `https://cinevoapp.com` بشكل طبيعي
   عندما يلائم النقاش (سؤال عن مكان المشاهدة، مقارنة، توصيات...).
4. **نبرة إنسانية**: يطابق لغة تعليق المستخدم (عربي يبقى عربي، إنجليزي
   يبقى إنجليزي...)، ولا يكشف أنه بوت.

---

## 📄 الترخيص والمسؤولية

هذا المشروع لأغراض أتمتة التفاعل مع حسابات تملكها/تديرها. التزم دائماً
بشروط استخدام كل منصة، واستخدم فترات الانتظار الآمنة لتجنّب الحظر.
