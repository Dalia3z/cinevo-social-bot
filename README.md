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
├── discover_targets.py      # سكريبت اكتشاف معرّفات الأفلام/المسلسلات
├── targets_loader.py        # قراءة/إدارة ملف الأهداف targets.json
├── targets.json             # تخزين المعرّفات المكتشفة (مقسّم ومنظّم)
├── setup_env.py             # إنشاء ملف .env تفاعلياً (اختبار محلي)
├── check_setup.py           # فحص تشخيصي محلي لكل المفاتيح (7 خطوات)
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
├── content_generator.py     # 🆕 توليد سكريبتات/كابشنات الفيديوهات القصيرة (AI)
├── content_planner.py       # 🆕 تقويم المحتوى اليومي + اختيار الأفلام
├── publish_queue.py         # 🆕 طابور SQLite للمحتوى (draft → ready → posted)
├── content_cli.py           # 🆕 واجهة أوامر لنظام المحتوى (لا تنشر تلقائياً)
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

## 🔍 اكتشاف الأهداف تلقائياً (Movies & TV IDs)

بدلاً من إدخال آلاف المعرّفات يدوياً في `.env`، استخدم سكريبت
[`discover_targets.py`](discover_targets.py) الذي يجلب معرّفات القنوات
والمقاطع (YouTube) والصفحات/المجتمعات (Meta) المتعلقة بالأفلام والمسلسلات.

### لماذا ملف `targets.json` منفصل؟
وضع 10,000 معرّف داخل سطر واحد في `.env` سيضخّم الملف ويعطّله. لذلك يُحفظ
الاكتشاف في ملف JSON مقسّم ومنظّم، ويقرأه البوت عبر
[`targets_loader.py`](targets_loader.py) بسلاسة.

### التشغيل

```bash
# اكتشاف يوتيوب فقط (حتى 10,000 عنصر)
python discover_targets.py --platform youtube --max 10000

# اكتشاف ميتا فقط
python discover_targets.py --platform meta

# اكتشاف الكل
python discover_targets.py --all --max 10000

# تجربة بدون حفظ (فحص فقط)
python discover_targets.py --dry-run
```

### ماذا يفعل السكريبت؟
1. **يوتيوب**: يستخدم YouTube Data API v3 مع استعلامات ديناميكية متعددة
   (مراجعة أفلام، ملخصات، شرح النهايات، أفضل الأفلام...) ويصفح النتائج حتى
   يصل للحد المطلوب، جامعاً معرّفات القنوات والمقاطع.
2. **ميتا**: يبحث في صفحات/مجتمعات السينما والترفيه عبر Graph API.
3. **التخزين**: يحفظ النتائج في [`targets.json`](targets.json) بهيكل مقسّم:
   `meta` (بيانات وصفية) / `youtube.channels` / `youtube.videos` /
   `facebook.facebook_pages` / `facebook.instagram_business`.
4. **تحديث `.env`**: يضبط `TARGETS_FILE=targets.json` ويفرّغ القوائم المضمّنة
   سابقاً ليجعل ملف JSON هو المصدر الوحيد.

> **ملاحظة**: بدون `YOUTUBE_API_KEY` أو `META_ACCESS_TOKEN`، يستخدم السكريبت
> قوائم بذور (Seed) آمنة حتى يبقى خط الأنابيب يعمل، ثم يمكنك إعادة تشغيله
> بالمفاتيح الحقيقية لتوسيع القائمة.

---

## ⚙️ الإعداد لكل منصة

### YouTube Shorts
- احصل على **API key** من Google Cloud Console (لقراءة التعليقات).
- للنشر (الرد) تحتاج **OAuth 2.0 token** بصلاحية `youtube.force-ssl`.
- شغّل [`discover_targets.py`](discover_targets.py) لاكتشاف القنوات/المقاطع،
  أو ضع معرّفات يدوياً في `YOUTUBE_VIDEO_IDS` / `YOUTUBE_CHANNEL_IDS`.

### Facebook & Instagram (Meta Graph API)
- أنشئ تطبيق Meta وأنشئ **Page access token** طويل الأمد.
- الصلاحيات المطلوبة: `pages_manage_posts`, `pages_read_engagement`,
  `instagram_basic`, `instagram_manage_comments`.
- شغّل [`discover_targets.py`](discover_targets.py) لاكتشاف الصفحات، أو ضع
  معرّفات يدوياً في `FACEBOOK_PAGE_IDS` / `INSTAGRAM_BUSINESS_IDS`.

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

## 🧪 الاختبار المحلي خطوة بخطوة (قبل النشر على GitHub Actions)

الهدف: التأكد أن **كل مفتاح يعمل 100%** على جهازك أولاً، ثم النشر على GitHub
Actions. لا تنشر شيئاً قبل أن ينجح الاختبار المحلي.

### الخطوة 0 — تثبيت المكتبات

```bash
cd cinevo-social-bot
pip install -r requirements.txt
```

### الخطوة 1 — إنشاء ملف `.env` تلقائياً

بدلاً من تعديل `.env` يدوياً (والتعرض لأخطاء المسافات/العلامات)، شغّل:

```bash
python setup_env.py
```

سيسألك عن:
1. `DEEPSEEK_API_KEY` — من https://platform.deepseek.com/api_keys
2. `YOUTUBE_API_KEY` — من Google Cloud Console (اقرأ الخطوة 3 أدناه)
3. `YOUTUBE_OAUTH_TOKEN` — اختياري (للنشر فقط؛ اتركه فارغاً للاختبار)
4. `ACTIVE_PLATFORMS` — اكتب `youtube` للاختبار المحلي

> ملف `.env` مُستثنى في [`.gitignore`](.gitignore) ولن يُرفع إلى GitHub أبداً.

### الخطوة 2 — تشغيل الفحص التشخيصي

```bash
python check_setup.py
```

يفحص 7 أشياء بالترتيب ويطبع `PASS` / `FAIL` / `WARN` لكل واحدة:

| # | الفحص | ماذا يعني |
|---|-------|-----------|
| 1 | وجود `.env` | الملف موجود وقابل للقراءة |
| 2 | المتغيرات المطلوبة | المفاتيح غير فارغة |
| 3 | DeepSeek API | نداء حقيقي — يجب أن يرد `OK` |
| 4 | YouTube API key | نداء حقيقي — يجب أن ينجح |
| 5 | YouTube OAuth | نداء حقيقي — يجب أن ينجح (أو WARN إن كان فارغاً) |
| 6 | `targets.json` | يوجد هدف واحد على الأقل |
| 7 | دورة Dry-run | توليد رد كامل **بدون نشر** |

النتيجة النهائية تكون إحدى:
- `READY 100% [OK]` — كل شيء يعمل، انتقل للخطوة 5.
- `READY (with warnings) [!]` — يعمل لكن لن ينشر (OAuth ناقص).
- `NOT READY [X]` — أصلح عناصر `FAIL` ثم أعد التشغيل.

### الخطوة 3 — إصلاح أخطاء YouTube (الأكثر شيوعاً)

إن ظهر `FAIL` في الفحص رقم 4، فالمشكلة في **المفتاح نفسه** وليس في معرّفات
القنوات. فعّل الواجهة:

1. اذهب إلى [Google Cloud Console](https://console.cloud.google.com/)
2. اختر المشروع الذي يحتوي على المفتاح
3. **APIs & Services → Library**
4. ابحث عن **YouTube Data API v3** واضغط **Enable**
5. **APIs & Services → Credentials** → افتح مفتاحك → تأكد أنه **بلا قيود**
   (أزل HTTP referrer / IP restrictions للاختبار)
6. انتظر 1–2 دقيقة ثم أعد `python check_setup.py`

### الخطوة 4 — تعبئة الأهداف (إن كان الفحص 6 فاشلاً)

```bash
python discover_targets.py --platform youtube --max 10000
```

### الخطوة 5 — النشر على GitHub Actions

بعد أن يصبح الفحص `READY 100%`:

1. تأكد أن أسرار GitHub مطابقة لملفك المحلي:
   **Settings → Secrets and variables → Actions** → أضف
   `DEEPSEEK_API_KEY`, `YOUTUBE_API_KEY`, `YOUTUBE_OAUTH_TOKEN`
2. اذهب إلى تبويب **Actions** → اختر **Cinevo Bot (scheduled)** →
   **Run workflow** (تشغيل يدوي للتجربة)
3. اقرأ خطوة **Run summary** — يجب أن ترى `DeepSeek calls` أكبر من صفر.

---

## 🎥 نظام المحتوى القصير (TikTok / YouTube Shorts)

> **معزول تماماً**: هذا النظام **منفصل 100%** عن بوت الرد على التعليقات.
> لا يشارك معه أي قاعدة بيانات، ولا ينشر أي شيء تلقائياً. تشغيله لا يؤثر
> إطلاقاً على حلقة الردود على YouTube.

### الفكرة

بدل انتظار التعليقات، هذا النظام **يولّد محتوى فيديو قصير جاهز** (Hook +
سكريبت + نص على الشاشة + كابشن + هاشتاغات + CTA) لأفلام/مسلسلات مناسبة
لموقع Cinevo. أنت تصوّر/تنشر يدوياً على TikTok و YouTube Shorts.

### الملفات

| الملف | الوظيفة |
|-------|---------|
| [`content_generator.py`](content_generator.py) | توليد الحزمة الكاملة عبر DeepSeek (JSON mode) |
| [`content_planner.py`](content_planner.py) | تقويم يومي + 8 زوايا محتوى + بنك أفلام منسّق |
| [`publish_queue.py`](publish_queue.py) | طابور SQLite منفصل (`content_queue.db`) |
| [`content_cli.py`](content_cli.py) | واجهة الأوامر (لا تنشر تلقائياً) |

### الأوامر

```bash
# 1) عرض تقويم محتوى لعدة أيام (بدون توليد AI)
python content_cli.py plan --days 7

# 2) توليد حزمة كاملة لفيلم معيّن وحفظها في الطابور
python content_cli.py generate --title "Inception" --angle ending_explained

# 3) عرض كل العناصر في الطابور
python content_cli.py list
python content_cli.py list --status ready

# 4) عرض عنصر واحد (سكريبت + نص على الشاشة)
python content_cli.py show --id 1

# 5) تصدير الكابشن + الهاشتاغات للنسخ واللصق
python content_cli.py export --id 1

# 6) تحديث الحالة بعد النشر
python content_cli.py mark --id 1 --status posted --platform tiktok \
    --url "https://www.tiktok.com/@cinevo/video/123"

# 7) إحصائيات الطابور
python content_cli.py stats
```

### دورة حياة العنصر

```
draft  →  ready  →  posted
              ↘  skipped
```

- **draft**: تم توليده، لم يُراجَع بعد.
- **ready**: راجعته وأنت جاهز للتصوير/النشر.
- **posted**: نشرته (مع رابط اختياري).
- **skipped**: قررت عدم استخدامه.

### زوايا المحتوى (8)

`hidden_gem`, `plot_twist`, `best_scene`, `ending_explained`,
`if_you_liked`, `true_story`, `one_take`, `watch_tonight`.

### إعدادات `.env`

| المتغير | الافتراضي | الوصف |
|---------|-----------|-------|
| `CONTENT_LANGUAGE` | `English` | لغة السكريبتات والكابشنات |
| `CONTENT_POSTS_PER_DAY` | `3` | عدد المنشورات في التقويم اليومي |
| `CONTENT_MAX_TOKENS` | `1200` | حد التوكنات لتوليد واحد |
| `CONTENT_TEMPERATURE` | `0.9` | درجة الإبداع (0.0–1.0) |

> **ملاحظة**: `content_queue.db` ملف وقت التشغيل ومُستثنى في `.gitignore`.

---

## 🩺 حل المشاكل (Troubleshooting)

### 1) البوت لا يعمل / DeepSeek usage = 0

افتح تبويب **Actions** في GitHub وشغّل الـ workflow يدوياً، ثم اقرأ خطوة
**Run summary**. ستخبرك بالسبب مباشرة:

| الرسالة في السجل | السبب | الحل |
|------------------|-------|------|
| `DEEPSEEK_API_KEY secret is MISSING` | الـ secret ناقص | أضفه في Settings → Secrets |
| `(none - DeepSeek was never called)` | لا توجد تعليقات (targets فارغة) | شغّل `discover_targets.py` |
| `YouTube API key self-test FAILED` | مفتاح YouTube غير صالح | راجع القسم 2 أدناه |
| `No AI reply generated` | فشل نداء DeepSeek | تحقق من المفتاح/الرصيد |
| `Cannot post ... OAUTH` | `YOUTUBE_OAUTH_TOKEN` ناقص | أضف التوكن |

### 2) خطأ `400 Bad Request` من YouTube

هذا الخطأ يعني أن **مفتاح `YOUTUBE_API_KEY` لا يعمل** (وليس أن معرّفات
القنوات خاطئة). البوت الآن يطبع **رد Google الخام** ليعطيك السبب الدقيق:

| رد Google | المعنى | الحل |
|-----------|--------|------|
| `API key not valid` | المفتاح خاطئ/ناقص | أنشئ مفتاحاً جديداً |
| `API has not been used ... disabled` | YouTube Data API v3 غير مفعّل | فعّله في Cloud Console |
| `requests from referer ... blocked` | قيود على المفتاح | أزل قيود HTTP referrer/IP |
| `quotaExceeded` | الحصة انتهت | انتظر أو استخدم مشروعاً آخر |

**خطوات تفعيل YouTube Data API v3:**
1. اذهب إلى [Google Cloud Console](https://console.cloud.google.com/)
2. اختر المشروع الذي يحتوي على المفتاح
3. **APIs & Services → Library**
4. ابحث عن **YouTube Data API v3**
5. اضغط **Enable** ✅
6. انتظر 1–2 دقيقة ثم أعد تشغيل الـ workflow

### 3) أخطاء OAuth عند النشر (401 / `unauthorized_client`)

النشر (الرد) يحتاج **OAuth 2.0** وليس مفتاح API. الأخطاء الشائعة:

| الرسالة في السجل | السبب | الحل |
|------------------|-------|------|
| `401 Unauthorized` على `comments.insert` | `YOUTUBE_OAUTH_TOKEN` منتهي/خاطئ | جدّد التوكن (انظر أدناه) |
| `unauthorized_client` عند refresh | الـ refresh token صادر من **OAuth client مختلف** عن `CLIENT_ID`/`SECRET` | استعمل نفس الـ client للثلاثة |
| `redirect_uri_mismatch` في Playground | `https://developers.google.com/oauthplayground` غير مسجّل | أضفه في Authorized redirect URIs |

**الحل الموصى به — سكربت محلي (بدون OAuth Playground):**

بدل الاعتماد على Google OAuth Playground (الذي يسبب `unauthorized_client`)،
استعمل السكربت [`get_youtube_refresh_token.py`](get_youtube_refresh_token.py):

```bash
python get_youtube_refresh_token.py --client-id YOUR_ID --client-secret YOUR_SECRET
```

1. سيطبع رابطاً — افتحه في المتصفح ووافق على الصلاحيات.
2. سيعود تلقائياً إلى `http://localhost:8080/` ويلتقط الـ `code`.
3. سيطبع **refresh token** مضمون أنه من **نفس** الـ client.

ثم أضف هذه الأسرار الثلاثة في GitHub (كلها من **نفس** الـ OAuth client):

| Secret | القيمة |
|--------|--------|
| `YOUTUBE_OAUTH_REFRESH_TOKEN` | الـ refresh token المطبوع |
| `YOUTUBE_OAUTH_CLIENT_ID` | نفس الـ Client ID |
| `YOUTUBE_OAUTH_CLIENT_SECRET` | نفس الـ Client Secret |

> **مهم**: استعمل OAuth client من نوع **Desktop app** (يقبل `http://localhost`
> تلقائياً)، أو **Web application** مع إضافة `http://localhost:8080/` في
> Authorized redirect URIs.

بعد إضافة الأسرار، البوت سيجدّد الـ access token تلقائياً في كل دورة
(`YouTube OAuth token refreshed successfully`).

### 4) الـ workflow توقف عن العمل بعد فترة

GitHub **يعطّل** الـ workflows المجدولة تلقائياً بعد **60 يوماً** من عدم
النشاط في المستودع. الحل: ملف
[`keepalive.yml`](.github/workflows/keepalive.yml) يقوم بعمل commit أسبوعي
لتجديد المؤقّت تلقائياً. إن توقف الـ workflow رغم ذلك، فعّله يدوياً من
تبويب **Actions → Enable workflow**.

> **ملاحظة**: جدولة GitHub (cron) هي "best-effort" وقد تتأخر أو تُتخطى،
> خاصة للجداول عالية التكرار مثل `*/30`. هذا سلوك طبيعي من GitHub.

---

## 📄 الترخيص والمسؤولية

هذا المشروع لأغراض أتمتة التفاعل مع حسابات تملكها/تديرها. التزم دائماً
بشروط استخدام كل منصة، واستخدم فترات الانتظار الآمنة لتجنّب الحظر.
