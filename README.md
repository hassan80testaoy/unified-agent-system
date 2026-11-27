# Unified Agent System (Phase 0 + Phase 1)

منصة وكلاء ذكاء اصطناعي شخصية + منصة سيو وباك لينك ذاتية الاستضافة، **عربية RTL أولاً** (مع تبديل فوري للإنجليزية)، تعمل محلياً على جهازك أولاً ثم تنتقل بنفس ملف `docker-compose.yml` إلى خادم **Ubuntu 24.04 VPS (Contabo Cloud VPS 6)**.

---

## المزايا المنفّذة في Phase 0 + Phase 1

- **لوحة تحكم عربي RTL أولاً (Next.js App Router + Tailwind):** شريط جانبي على اليمين، خصائص CSS المنطقية فقط (`start/end/ms/me`)، خطوط `Noto Kufi Arabic` و `JetBrains Mono`، أرقام غربية/عربية هندية، تقويم ميلادي/هجري، وضع ليلي/نهاري.
- **راوتر وتفويض متعدد المزودين (Multi-Provider Orchestration):** `config/providers.example.yaml` و `config/models.yaml` (مفاتيح API فقط، بدون استخدام اشتراكات شخصية)، مع تعيين **Orchestrator** و **Workers حسب نوع المهمة** و **Reviewer من مزود مختلف** لكل وكيل من الإعدادات بدون تعديل الكود، وحدود ميزانية يومية/شهرية تتوقف تلقائياً عند السقف.
- **سجل الوكلاء المرن (Plugin Registry) + نواة Instinct:**
  - `backlink` (P0): وضع محاكاة آمن + وضع Playwright، فحص `noindex` قبل التسجيل، هوية `"خبير سيو معاذ حسن"` (`دكتور السيو`)، أنكرات موزونة للرابط الرئيسي وخرائط جوجل، بايو متنوع بمراجعة مزود مستقل، تسجيل كل خطوة في `bl_steps` بدون أسرار، وفحص اللينك الحي `dofollow/nofollow`.
  - `link_monitor` (P0): إعادة فحص الروابط الحية.
  - كتالوج الوكلاء المقترحة (20 وكيل SEO) + سجل المهارات (38 مهارة Instinct + 18 دليل مواقع).
- **حسابات Google والتحكم بالإيميل (§2.3):** إضافة آمنة عبر نموذج Vault مشفر بـ Fernet، بروفايل متصفح مخصص لكل حساب، تدفق طلب كود التحقق (إيقاف التشغيل ← سؤال المستخدم ← استخدام مرة واحدة ← مسح فوري من الذاكرة بدون تخزين/لوج، حد أقصى محاولتين، معالجة المهلة)، وفصل/سحب الجلسة يمسح الأسرار والبروفايل فوراً.
- **وحدة البريد (Mailboxes):** IMAP قراءة فقط (`readonly=True`) + تشفير Fernet + `+alias` لكل موقع + اختبار عند الربط.
- **الكابتشا بنظام Plugin Interface (`captcha/solver.py`):**
  - التنفيذ الافتراضي هو **`ManualSolver` (Human-in-the-Loop)** عبر المتصفح الحي `noVNC` على `127.0.0.1:6080` خلف نفق SSH فقط، مع إحصائيات حقيقية (`captcha_attempts`) وتنبيه الداشبورد و Telegram.
- **الأمان (F1–F5):** مصادقة 2FA TOTP، فلتر تنظيف أسرار شامل في السجلات والأحداث، Kill Switch عام يوقف التشغيل خلال ثوانٍ، وحماية الخزنة (`Blind-Fill Vault`) المربوطة بالنطاقات.

---

## التشغيل المحلي المباشر على Windows (بدون Docker)

المتطلبات: `Python 3.12+` و `Node.js 20+`.

```powershell
cd unified-agent-system
powershell -ExecutionPolicy Bypass -File .\scripts\dev-windows.ps1
```

ثم افتح المتصفح على:
- لوحة التحكم: `http://127.0.0.1:3010`
- صحة الـ API: `http://127.0.0.1:8010/api/health`

لأول دخول: ستظهر شاشة **إعداد حساب المالك + تفعيل 2FA**.

---

## التشغيل المحلي عبر Docker Compose

```bash
cp .env.example .env
# عبّئ المفاتيح وكلمات المرور يدوياً داخل .env ثم:
chmod 600 .env
docker compose up -d
docker compose ps
curl http://127.0.0.1:3010/api/health
```

*(ملف `caddy` يعمل فقط مع `--profile prod`، و `ollama` يعمل فقط مع `--profile local-llm`.)*

---

## النشر والترحيل إلى Ubuntu 24.04 VPS (Contabo)

1. **تجهيز السيرفر لأول مرة (كـ `root` بعد إضافة مفتاح SSH):**
   ```bash
   bash scripts/setup-server.sh
   ```
2. **نسخ احتياطي من جهازك المحلي ونقل المشروع:**
   ```bash
   ./scripts/backup.sh
   scp -r . deploy@YOUR_SERVER_IP:/opt/unified-agent-system/
   ```
3. **تشغيل الإنتاج بـ HTTPS التلقائي:**
   ```bash
   cd /opt/unified-agent-system
   # ضع DOMAIN=app.yourdomain.com والمفاتيح يدوياً في .env (chmod 600 .env)
   docker compose --profile prod up -d
   ./scripts/restore.sh ./backups/latest.tar.gz.enc
   ```
4. **فتح نافذة المتصفح الحي (`noVNC`) لحل الكابتشا يدوياً:**
   > **تحذير أمني:** المنفذ `6080` مربوط بـ `127.0.0.1` فقط ولا يُفتح للإنترنت أبداً.
   ```bash
   ssh -L 6080:127.0.0.1:6080 deploy@YOUR_SERVER_IP
   # ثم افتح على جهازك: http://127.0.0.1:6080/vnc.html
   ```

---

## الاختبارات والتحقق

```bash
# اختبارات الباك إند
cd backend
.\.venv\Scripts\pytest.exe tests -v

# فحص الأمان و RTL و سياسة الكابتشا
cd ..
.\backend\.venv\Scripts\python.exe scripts\verify-security.py

# بناء واجهة Next.js
cd web
npm run build
```
