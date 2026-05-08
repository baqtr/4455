# Heroku XMR afdaa Stable Low v8

هذه النسخة مصممة لحل المشاكل التي ظهرت في السجل:

```text
XMRig خرج بالكود: -9
Process exited with status 137
unrecognized option: 1
```

## ما الذي تم إصلاحه؟

- إصلاح خطأ `unrecognized option: 1` بإرسال خيارات XMRig بصيغة صحيحة.
- إجبار التعدين على `threads=1` فقط.
- استخدام `RandomX light` لتقليل استهلاك الذاكرة.
- إضافة micro-throttle: تشغيل قصير جدًا ثم إيقاف مؤقت لتقليل الاستهلاك.
- منع `worker.2` و `worker.3` من تشغيل التعدين إذا نسيت worker أكثر من 1.
- إبقاء worker حيًا إذا قُتل XMRig، مع إعادة محاولة آمنة.
- قراءة المحفظة من `config.env` أو `wallet.txt`.

## أين أضع عنوان المحفظة؟

افتح ملف:

```bash
config.env
```

وغيّر هذا السطر:

```bash
XMR_WALLET="PUT_YOUR_XMR_RECEIVE_ADDRESS_HERE"
```

إلى عنوان محفظتك XMR من زر Receive:

```bash
XMR_WALLET="عنوان_XMR_الخاص_بك"
```

أو ضع عنوانك فقط داخل ملف:

```bash
wallet.txt
```

لا تضع كلمات الاسترداد أو المفتاح الخاص.

## الإعداد المهم في Heroku

من لوحة Heroku:

```text
Resources -> worker -> Quantity = 1
```

لا تجعلها 2 أو أكثر.

## كيف أعرف أنه يعمل؟

افتح Logs. إذا ظهر:

```text
new job from pool.supportxmr.com
accepted
```

فالتعدين يعمل.

## إذا استمر XMRig يخرج بالكود -9

عدّل `config.env` إلى أبطأ إعداد:

```bash
THROTTLE_WORK_SECONDS="0.10"
THROTTLE_SLEEP_SECONDS="5.00"
```

ثم أعد النشر. الربح سيكون قليل جدًا، لكن الثبات أعلى.

## ملاحظة مهمة

إذا كانت منصة Heroku نفسها تقتل أي عملية تعدين على مستوى السياسة أو حدود dyno، فلا يمكن لأي ملفات ضمان تشغيل دائم. هذه النسخة تقلل الاستهلاك وتمنع crash loop قدر الإمكان.
