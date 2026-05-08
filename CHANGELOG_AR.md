# v8 Stable Low

## تغييرات مهمة

- تصحيح خيارات XMRig التي سببت: `unrecognized option: 1`.
- حذف خيارات قد تسبب مشاكل على Heroku مثل `--cpu-max-threads-hint 1` بصيغة خاطئة.
- استخدام صيغة `--threads=1` و `--randomx-mode=light`.
- إضافة micro-throttle عبر SIGSTOP/SIGCONT.
- منع تعدد العمال من التعدين تلقائيًا.
- وضع افتراضي منخفض جدًا: 0.20s عمل / 2.00s توقف.
- Backoff تلقائي إلى 0.10s عمل / 5.00s توقف عند القتل.
