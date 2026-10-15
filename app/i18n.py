"""Interface language. English is the default; Arabic is a setting.

Every user-visible string lives here as a (english, arabic) pair, including the
ones that get spoken, so a translation can never drift out of sync with the code
that uses it. `t("key")` returns the string for the current language, and any
`{placeholders}` are filled from the keyword arguments.

Changing the language at runtime is supported: widgets are rebuilt rather than
relabelled, because a right-to-left layout is not just different text.
"""
from __future__ import annotations

UI_LANGUAGES = ["en", "ar"]
UI_LANGUAGE_NAMES = {"en": "English", "ar": "العربية (Arabic)"}

_current = "en"


def set_language(code: str) -> str:
    global _current
    _current = code if code in UI_LANGUAGES else "en"
    return _current



def is_rtl_ui() -> bool:
    return _current == "ar"


def t(_id: str, **kw) -> str:
    """The lookup id is named `_id`, not `key`, because several strings take a
    `{key}` placeholder for a keyboard shortcut and the two would collide."""
    pair = S.get(_id)
    if pair is None:
        return _id                     # a missing id shows itself, loudly
    text = pair[1] if _current == "ar" and len(pair) > 1 and pair[1] else pair[0]
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError):
            return text
    return text


def duration(seconds: float) -> str:
    """A duration a screen reader reads as words, not as a run of digits."""
    seconds = max(0, int(seconds))
    h, m, s = seconds // 3600, seconds % 3600 // 60, seconds % 60
    if _current == "ar":
        bits = []
        if h:
            bits.append(f"{h} ساعة")
        if m:
            bits.append(f"{m} دقيقة")
        if s and not h:
            bits.append(f"{s} ثانية")
        return " و".join(bits) if bits else "أقل من ثانية"
    bits = []
    if h:
        bits.append(f"{h} hour" + ("s" if h != 1 else ""))
    if m:
        bits.append(f"{m} minute" + ("s" if m != 1 else ""))
    if s and not h:
        bits.append(f"{s} second" + ("s" if s != 1 else ""))
    return " and ".join(bits) if bits else "less than a second"


# --------------------------------------------------------------------------
# (english, arabic)
# --------------------------------------------------------------------------
S: dict[str, tuple[str, str]] = {
    "app_name": ("Sada", "صدى"),

    # ---------------------------------------------------------------- models
    "model_turbo": ("turbo - fast and accurate (recommended)",
                    "turbo - سريع ودقيق (موصى به)"),
    "model_large-v3": ("large-v3 - most accurate, slower",
                       "large-v3 - الأدق وأبطأ"),
    "model_medium": ("medium - middle ground", "medium - متوسط"),
    "model_small": ("small - for slower machines, and the default for dictation",
                    "small - للأجهزة الضعيفة، وافتراضي الإملاء"),
    "model_tiny": ("tiny - fastest, least accurate",
                   "tiny - الأسرع والأقل دقة"),

    "device_auto": ("Automatic - graphics card if present, otherwise processor",
                    "تلقائي - كارت الرسوم إن وُجد، وإلا المعالج"),
    "device_cuda": ("Graphics card only", "كارت الرسوم فقط"),
    "device_cpu": ("Processor only", "المعالج فقط"),

    "insert_cursor": ("At the cursor", "عند مؤشر الكتابة"),
    "insert_end": ("At the end of the document", "في آخر المستند"),
    "insert_new": ("In a new document", "في مستند جديد"),

    "lang_auto": ("Detect automatically (recommended)", "كشف تلقائي (موصى به)"),
    "lang_ar": ("Arabic", "العربية"),
    "lang_en": ("English", "الإنجليزية"),
    "lang_fr": ("French", "الفرنسية"),
    "lang_de": ("German", "الألمانية"),
    "lang_es": ("Spanish", "الإسبانية"),
    "lang_tr": ("Turkish", "التركية"),
    "lang_ur": ("Urdu", "الأردية"),
    "lang_fa": ("Persian", "الفارسية"),

    # -------------------------------------------------------------- settings
    "settings_title": ("Sada settings", "إعدادات صدى"),
    "group_engine": ("Engine and accuracy", "المحرك والدقة"),
    "group_output": ("Transcript", "النص الناتج"),
    "group_shortcuts": ("Keyboard shortcuts", "اختصارات لوحة المفاتيح"),
    "group_reader": ("Screen reader", "قارئ الشاشة"),
    "group_interface": ("Interface", "الواجهة"),

    "field_ui_language": ("Interface language:", "لغة الواجهة:"),
    "help_ui_language": ("Changes the language of the windows and the spoken "
                         "messages. Takes effect as soon as you save.",
                         "يغيّر لغة النوافذ والرسائل المنطوقة. يُطبَّق بمجرد الحفظ."),
    "field_model": ("Model:", "النموذج:"),
    "help_model": ("Larger models are more accurate and slower. turbo is the "
                   "recommended balance.",
                   "النماذج الأكبر أدق وأبطأ. turbo هو التوازن الموصى به."),
    "field_device": ("Run on:", "المعالجة على:"),
    "help_device": ("Automatic uses the graphics card when there is one, and the "
                    "processor otherwise.",
                    "تلقائي يستخدم كارت الرسوم إن وُجد وإلا المعالج."),
    "field_language": ("Audio language:", "لغة الصوت:"),
    "help_language": ("Automatic detection works out the language from the audio "
                      "itself.", "الكشف التلقائي يحدد اللغة من الصوت نفسه."),
    "field_hotwords": ("Names and terms:", "مصطلحات وأسماء:"),
    "help_hotwords": ("Words that help the model spell names and jargon "
                      "correctly, separated by spaces.",
                      "كلمات تساعد النموذج على كتابة الأسماء والمصطلحات بشكل صحيح، "
                      "مفصولة بمسافات."),

    "field_insert": ("Insert the text:", "مكان الإدراج:"),
    "check_timestamps": ("Put the time before each paragraph",
                         "إضافة الوقت قبل كل فقرة"),
    "check_rtl": ("Set right-to-left reading order automatically for Arabic",
                  "ضبط اتجاه الكتابة من اليمين لليسار تلقائيًا للعربية"),
    "check_side_files": ("Save a text file and a subtitle file next to the audio",
                         "حفظ نسخة نصية وملف ترجمة بجانب الملف الصوتي"),

    "field_k_transcribe": ("Start transcribing:", "بدء التفريغ:"),
    "help_k_transcribe": ("For example Ctrl+Shift+T. At least one modifier key "
                          "is required.",
                          "مثال: Ctrl+Shift+T. لا بد من مفتاح مُعدِّل واحد على الأقل."),
    "field_k_window": ("Open the program window:", "فتح نافذة البرنامج:"),
    "field_k_settings": ("Open settings:", "فتح الإعدادات:"),
    "field_k_status": ("Read the status:", "قراءة الحالة:"),
    "help_k_status": ("Speaks what is happening and how far along it is.",
                      "ينطق حالة العمل الجاري ونسبة التقدّم."),
    "field_k_cancel": ("Cancel the current job:", "إلغاء العمل الجاري:"),

    # ------------------------------------------------------------ dictation
    "group_dictation": ("Dictation", "الإملاء"),
    "field_k_dictate": ("Start and stop dictation:", "بدء الإملاء وإيقافه:"),
    "help_k_dictate": ("One shortcut for both: press it to start speaking, "
                       "press it again to stop.",
                       "اختصار واحد للاثنين: اضغطه لتبدأ الكلام، واضغطه مرة "
                       "أخرى لتتوقف."),
    "field_dictation_model": ("Model used for dictation:",
                              "النموذج المستخدم للإملاء:"),
    "help_dictation_model": ("Dictation needs a model that runs faster than you "
                             "speak, which is not the same choice as for a file. "
                             "On a processor without a graphics card, turbo runs "
                             "slower than real time, so the text keeps falling "
                             "further behind. small keeps up; tiny is instant but "
                             "makes visible mistakes.",
                             "يحتاج الإملاء نموذجًا أسرع من كلامك، وهو اختيار "
                             "مختلف عن اختيار الملفات. على معالج بلا كارت رسوم، "
                             "turbo أبطأ من الزمن الحقيقي فيتأخّر النص أكثر "
                             "وأكثر. small يلاحق الكلام، وtiny فوري لكنه يخطئ "
                             "بشكل ملحوظ."),
    "field_target": ("Gather before writing (seconds):",
                     "التجميع قبل الكتابة (ثانية):"),
    "help_target": ("Whisper costs almost the same for two seconds of audio as "
                    "for twenty, so writing after every short pause is much "
                    "slower overall. A deliberate silence always writes "
                    "immediately, whatever this is set to.",
                    "تكلفة ويسبر لثانيتين من الصوت تقارب تكلفته لعشرين، فالكتابة "
                    "بعد كل سكتة قصيرة أبطأ بكثير في المجموع. والسكوت المتعمَّد "
                    "يكتب فورًا مهما كانت هذه القيمة."),
    "say_dictation_model_missing": ("The dictation model {name} is not downloaded. "
                                    "Open settings to download it.",
                                    "نموذج الإملاء {name} غير مُنزَّل. افتح "
                                    "الإعدادات لتنزيله."),
    "say_dictation_loading": ("Preparing the dictation model. This happens once.",
                              "جارٍ تهيئة نموذج الإملاء. يحدث هذا مرة واحدة."),
    "say_dictation_pending": ("Stop. {n} phrases still being written.",
                              "إيقاف. ما زالت {n} عبارة تُكتب."),
    "field_mic": ("Microphone:", "الميكروفون:"),
    "mic_auto": ("Choose automatically", "اختيار تلقائي"),
    "mic_none": ("No microphone was found", "لم يُعثر على ميكروفون"),
    "help_mic": ("Which input to record from. Automatic takes the first one "
                 "Windows reports.",
                 "الجهاز الذي يُسجَّل منه. التلقائي يأخذ أول جهاز يذكره ويندوز."),
    "field_pause": ("Pause that ends a phrase (milliseconds):",
                    "السكتة التي تُنهي العبارة (ميلي ثانية):"),
    "help_pause": ("How long you have to stop speaking before the phrase is "
                   "written. Lower is faster but may cut a sentence where you "
                   "drew breath.",
                   "المدة التي تتوقف فيها عن الكلام قبل كتابة العبارة. الأقل "
                   "أسرع لكنه قد يقطع الجملة عند أخذ النفس."),
    "check_fillers": ("Remove hesitation sounds such as um and ممم",
                      "إزالة أصوات التردّد مثل ممم و um"),
    "help_fillers": ("Only sounds that are not words in any language are "
                     "removed. Real words, including يعني "
                     "and اه, are always kept.",
                     "تُزال الأصوات التي ليست كلمات في أي لغة فقط. الكلمات "
                     "الحقيقية، ومنها يعني و اه، "
                     "تبقى دائمًا."),
    "check_speak_phrases": ("Read each phrase aloud as it is written",
                            "نطق كل عبارة عند كتابتها"),
    "help_speak_phrases": ("Word does not announce text that a program inserts, "
                           "so without this you would not hear what was written.",
                           "لا يُعلن وورد عن نص يُدرجه برنامج آخر، فبدون هذا لن "
                           "تسمع ما كُتب."),

    "undo_dictation": ("Dictated phrase", "عبارة مُملاة"),
    "tray_dictating": ("Dictating", "جارٍ الإملاء"),
    "tray_dictate": ("Dictate", "إملاء"),
    "say_transcribe_busy_dictating": ("Stop dictation first, then transcribe a file.",
                                      "أوقف الإملاء أولًا ثم فرّغ ملفًا."),

    "say_dictation_start": ("Start dictation", "بدء الإملاء"),
    "say_dictation_stop": ("Stop", "إيقاف"),
    "say_dictation_busy": ("Wait for the transcription to finish, then dictate.",
                           "انتظر انتهاء التفريغ ثم أمْلِ."),
    "say_dictation_failed": ("Dictation stopped. {error}",
                             "توقف الإملاء. {error}"),
    "say_dictation_status": ("Dictating. {n} phrases, {w} words so far.",
                             "جارٍ الإملاء. {n} عبارة و{w} كلمة حتى الآن."),

    "status_dictating": ("Dictating - {n} phrases, {w} words",
                         "جارٍ الإملاء - {n} عبارة، {w} كلمة"),

    "check_speak": ("Speak the status and progress automatically",
                    "نطق الحالة والتقدّم تلقائيًا"),
    "field_progress_every": ("Speak progress every (seconds):",
                             "نطق التقدّم كل (ثانية):"),
    "help_progress_every": ("Zero means progress is never spoken; you can still "
                            "ask for it with its shortcut.",
                            "صفر يعني لا يُنطق التقدّم إطلاقًا؛ تبقى قراءة الحالة "
                            "باختصارها."),
    "check_sapi": ("Use the Windows voice if no screen reader is found",
                   "استخدام صوت ويندوز إذا لم يُعثر على قارئ شاشة"),
    "check_only_word": ("Shortcuts work only while Word is in front",
                        "تعمل الاختصارات فقط عندما يكون وورد في المقدمة"),
    "detected_reader": ("Screen reader detected: {name}",
                        "قارئ الشاشة المكتشف حاليًا: {name}"),

    "btn_save": ("Save", "حفظ"),
    "btn_reset": ("Reset to defaults", "استعادة الإعدادات الافتراضية"),
    "reset_confirm_title": ("Reset settings", "استعادة الإعدادات"),
    "reset_confirm": ("Put every setting back to its default? The interface "
                      "language will go back to English.",
                      "هل تريد إرجاع كل الإعدادات إلى وضعها الافتراضي؟ "
                      "ستعود لغة الواجهة إلى الإنجليزية."),
    "say_reset": ("Settings reset to defaults.",
                  "تمت استعادة الإعدادات الافتراضية."),
    "delete_done": ("{name} deleted.", "تم حذف {name}."),
    "delete_failed": ("{name} could not be deleted. It may be in use - close "
                      "Sada and try again.",
                      "تعذّر حذف {name}. قد يكون قيد الاستخدام - أغلق صدى "
                      "ثم أعد المحاولة."),

    # ------------------------------------------------------------ model files
    "group_models": ("Model files", "ملفات النماذج"),
    "model_installed": ("installed", "مُنزَّل"),
    "model_not_installed": ("not installed", "غير مُنزَّل"),
    "model_partial": ("incomplete download", "تحميل غير مكتمل"),
    "col_model": ("Model", "النموذج"),
    "col_size": ("Size", "الحجم"),
    "col_state": ("State", "الحالة"),
    "btn_download": ("Download", "تنزيل"),
    "btn_delete": ("Delete", "حذف"),
    "btn_stop_download": ("Stop the download", "إيقاف التنزيل"),
    "downloading": ("Downloading {name}: {done} of {total}",
                    "جارٍ تنزيل {name}: {done} من {total}"),
    "dl_progress_name": ("Download progress", "تقدّم التنزيل"),
    "dl_line_file": ("Downloading {name}", "جارٍ تنزيل {name}"),
    "dl_line_amount": ("{done} of {total} - {pct} percent",
                       "{done} من {total} - {pct} بالمئة"),
    "dl_line_speed": ("Speed: {speed} per second", "السرعة: {speed} في الثانية"),
    "dl_line_eta": ("About {eta} remaining", "يتبقى حوالي {eta}"),
    "dl_line_eta_calc": ("Measuring the speed", "جارٍ قياس السرعة"),
    "say_dl_start": ("Started downloading {name}, {total}. This happens once.",
                     "بدأ تنزيل {name}، {total}. يحدث هذا مرة واحدة فقط."),
    "say_dl_progress": ("{pct} percent. About {eta} remaining.",
                        "{pct} بالمئة. يتبقى حوالي {eta}."),
    # separate sentences, because substituting "still measuring" into
    # "about {eta} remaining" produces nonsense
    "say_dl_progress_calc": ("{pct} percent. Still measuring the speed.",
                             "{pct} بالمئة. جارٍ قياس السرعة."),
    "say_dl_almost": ("{pct} percent. Almost done.",
                      "{pct} بالمئة. على وشك الانتهاء."),
    "say_dl_done": ("{name} downloaded. {total} in {elapsed}.",
                    "تم تنزيل {name}. {total} في {elapsed}."),
    "deleting": ("Deleting {name}...", "جارٍ حذف {name}..."),
    "say_deleting": ("Deleting {name}, {size}.", "جارٍ حذف {name}، {size}."),
    "delete_done_detail": ("{name} deleted. {size} freed.",
                           "تم حذف {name}. تم تحرير {size}."),
    "download_done": ("{name} downloaded.", "تم تنزيل {name}."),
    "download_failed": ("{name} could not be downloaded. {error}",
                        "تعذّر تنزيل {name}. {error}"),
    "download_cancelled": ("Download stopped.", "أُوقف التنزيل."),
    "delete_confirm": ("Delete the file for {name}? You can download it again "
                       "later.",
                       "هل تريد حذف ملف {name}؟ يمكنك تنزيله مرة أخرى لاحقًا."),
    "model_missing_title": ("Model not installed", "النموذج غير مُنزَّل"),
    "model_missing": ("{name} is not on this machine. Open Settings, choose it "
                      "under Model files and press Download.",
                      "النموذج {name} غير موجود على هذا الجهاز. افتح الإعدادات "
                      "واختره من «ملفات النماذج» ثم اضغط تنزيل."),
    "model_not_installed_hint": (" - not installed", " - غير مُنزَّل"),

    # ------------------------------------------------------------------- jobs
    "group_jobs": ("Jobs", "الأعمال"),
    "jobs_name": ("List of jobs", "قائمة الأعمال"),
    "col_file": ("File", "الملف"),
    "col_progress": ("Progress", "التقدّم"),
    "col_language": ("Language", "اللغة"),
    "col_eta": ("Time remaining", "الوقت المتوقع"),
    "btn_cancel_selected": ("Cancel the selected job", "إلغاء العمل المحدَّد"),
    "btn_clear_finished": ("Clear finished jobs", "إخفاء الأعمال المنتهية"),
    "jstate_queued": ("Waiting its turn", "في انتظار الدور"),
    "jstate_running": ("Working", "جارٍ العمل"),
    "jstate_cancelling": ("Cancelling", "جارٍ الإلغاء"),
    "jstate_done": ("Finished", "انتهى"),
    "jstate_cancelled": ("Cancelled", "أُلغي"),
    "jstate_error": ("Failed", "فشل"),
    "say_queued": ("{name} added to the queue. {n} jobs waiting.",
                   "تمت إضافة {name} إلى الطابور. {n} أعمال في الانتظار."),
    "say_job_cancelling": ("Cancelling {name}. You can start another file now.",
                           "جارٍ إلغاء {name}. يمكنك بدء ملف آخر الآن."),
    "say_nothing_selected": ("No job is selected.", "لا يوجد عمل محدَّد."),
    "line_queue": ("{n} jobs in the queue", "{n} أعمال في الطابور"),
    "btn_cancel": ("Cancel", "إلغاء"),
    "btn_help": ("Help", "المساعدة"),
    "btn_close": ("Close", "إغلاق"),

    "bad_setting_title": ("Invalid setting", "إعداد غير صالح"),
    "bad_hotkey": ("The shortcut “{key}” is not valid. Use at least one "
                   "modifier, such as Ctrl+Shift+T.",
                   "الاختصار «{key}» غير صالح. استخدم مُعدِّلًا واحدًا على الأقل، "
                   "مثل Ctrl+Shift+T."),
    "dup_hotkey": ("The shortcut “{key}” is used more than once. Each "
                   "command needs its own.",
                   "الاختصار «{key}» مُستخدم أكثر من مرة. لكل أمر اختصار مختلف."),

    # ------------------------------------------------------------ main window
    "window_title": ("Sada", "صدى"),
    "group_status": ("Status", "الحالة"),
    "group_actions": ("Commands", "الأوامر"),
    "status_name": ("Program status", "حالة البرنامج"),
    "btn_transcribe": ("&Start transcribing   ({key})", "&بدء التفريغ   ({key})"),
    "btn_status": ("&Read the status   ({key})", "&قراءة الحالة   ({key})"),
    "btn_cancel_job": ("Cancel the current &job   ({key})",
                       "إل&غاء العمل الجاري   ({key})"),
    "btn_settings": ("S&ettings   ({key})", "الإ&عدادات   ({key})"),
    "btn_help_f1": ("&Help   (F1)", "الم&ساعدة   (F1)"),
    "btn_about": ("&About", "&حول البرنامج"),
    "btn_quit": ("E&xit the program", "&خروج من البرنامج"),
    "btn_hide": ("Hide this window (the program keeps running)",
                 "إخفاء النافذة (يبقى البرنامج يعمل)"),
    "about_title": ("About Sada", "حول صدى"),
    "about_name": ("Program information", "معلومات عن البرنامج"),
    "help_title": ("Sada help", "مساعدة صدى"),
    "help_name": ("Help text", "نص المساعدة"),

    # --------------------------------------------------------------- progress
    "preparing": ("Preparing…", "جارٍ التحضير…"),
    "cancelling": ("Cancelling…", "جارٍ الإلغاء…"),

    # ------------------------------------------------------------- job states
    "state_queued": ("Waiting", "في الانتظار"),
    "state_decoding": ("Reading the audio file", "جارٍ قراءة الملف الصوتي"),
    "state_detecting": ("Detecting the language", "جارٍ كشف اللغة"),
    "state_transcribing": ("Transcribing", "جارٍ التفريغ"),
    "state_writing": ("Writing the text", "جارٍ كتابة النص"),
    "state_done": ("Finished", "انتهى"),
    "state_error": ("Something went wrong", "حدث خطأ"),
    "state_cancelled": ("Cancelled", "أُلغي"),

    "line_language": ("Language: {lang}", "اللغة: {lang}"),
    "line_duration": ("Length: {dur}", "المدة: {dur}"),
    "line_progress": ("Progress: {pct} percent", "التقدّم: {pct} بالمئة"),
    "line_eta": ("Time remaining: {eta}", "الوقت المتوقع: {eta}"),
    "line_speed": ("Speed: {x} times real time", "السرعة: {x} ضعف الزمن الحقيقي"),
    "line_eta_calc": ("Time remaining: still measuring",
                      "الوقت المتوقع: جارٍ القياس"),
    "line_speed_calc": ("Speed: still measuring", "السرعة: جارٍ القياس"),
    "line_file": ("File: {name}", "الملف: {name}"),
    "line_words": ("Words: {n}", "عدد الكلمات: {n}"),
    "line_model": ("Model: {model}", "النموذج: {model}"),
    "line_runs_on": ("Running on: {device}", "المعالجة على: {device}"),
    "line_reader": ("Screen reader: {name}", "قارئ الشاشة: {name}"),

    # ----------------------------------------------------------------- spoken
    "tray_ready": ("Ready", "جاهز"),
    "tray_working": ("Transcribing", "جارٍ التفريغ"),
    "tray_loading": ("Loading the model", "جارٍ تحميل النموذج"),
    "tray_load_failed": ("The model failed to load", "فشل تحميل النموذج"),
    "tray_window": ("Program window", "نافذة البرنامج"),
    "tray_settings": ("Settings", "الإعدادات"),
    "tray_help": ("Help", "المساعدة"),
    "tray_quit": ("Exit", "خروج"),

    "pick_file_title": ("Choose an audio or video file",
                        "اختر الملف الصوتي أو المرئي"),
    "wildcard_all_media": ("All audio and video files",
                           "كل الملفات الصوتية والمرئية"),
    "wildcard_audio": ("Audio files", "ملفات صوتية"),
    "wildcard_video": ("Video files", "ملفات مرئية"),
    "wildcard_any": ("All files", "كل الملفات"),

    "say_pick_file": ("Choose the audio file.", "اختر الملف الصوتي."),
    "say_pick_cancelled": ("Selection cancelled.", "أُلغي الاختيار."),
    "say_started": ("Started transcribing {name}.", "بدأ تفريغ الملف {name}."),
    "say_loading": ("The transcription model is still loading. Wait a moment and "
                    "try again.",
                    "جارٍ تحميل نموذج التفريغ، انتظر قليلًا ثم أعد المحاولة."),
    "say_load_failed": ("The transcription model could not be loaded. {error}",
                        "تعذّر تحميل نموذج التفريغ. {error}"),
    "say_progress": ("{pct} percent. About {eta} remaining.",
                     "{pct} بالمئة. الوقت المتوقع للانتهاء {eta}."),
    "say_ready": ("The program is ready. Model {model}, running on {device}.",
                  "البرنامج جاهز. النموذج {model}، المعالجة على {device}."),
    "say_working": ("Transcribing {name}. {pct} percent. Language {lang}. About "
                    "{eta} remaining.",
                    "جارٍ تفريغ {name}. {pct} بالمئة. اللغة {lang}. "
                    "الوقت المتوقع للانتهاء {eta}."),
    "say_done": ("Transcription finished. {words} words, language {lang}. The text "
                 "was inserted into {doc}, and the cursor is at the start of it.",
                 "انتهى التفريغ. {words} كلمة، اللغة {lang}. "
                 "أُدرج النص في المستند {doc}، ومؤشر الكتابة في أول النص."),
    "say_cancelled": ("Transcription cancelled. The work done so far is saved.",
                      "أُلغي التفريغ. ما تم إنجازه محفوظ."),
    "say_nothing_to_cancel": ("There is no job running to cancel.",
                              "لا يوجد عمل جارٍ لإلغائه."),
    "say_error": ("Something went wrong. {error}", "حدث خطأ. {error}"),
    "say_settings_saved": ("Settings saved.", "حُفظت الإعدادات."),
    "say_settings_cancelled": ("Settings cancelled.", "أُلغيت الإعدادات."),
    "say_model_switch": ("Loading the new model.", "جارٍ تحميل النموذج الجديد."),
    "say_model_switch_later": ("The new model will be used after the current job "
                              "finishes.",
                              "سيُطبَّق النموذج الجديد بعد انتهاء العمل الجاري."),
    # Spoken once at startup. This is how someone who cannot see the window
    # learns what the program can do, so a new feature that is not named here
    # is a feature nobody finds.
    "say_welcome": ("{app} is running. Press {k_start} to transcribe a file, "
                    "{k_dictate} to dictate, "
                    "{k_window} for the program window, {k_settings} for settings, "
                    "and {k_status} to hear the status.",
                    "{app} يعمل الآن. اضغط {k_start} لتفريغ ملف، "
                    "و{k_dictate} للإملاء، "
                    "و{k_window} لفتح نافذة البرنامج، و{k_settings} للإعدادات، "
                    "و{k_status} لقراءة الحالة."),


    "hotkeys_failed": ("These shortcuts could not be registered, which usually "
                       "means another program is using them: {keys}",
                       "تعذّر تسجيل الاختصارات التالية، ويبدو أن برنامجًا آخر "
                       "يستخدمها: {keys}"),
    "already_running": ("The program is already running. You will find it in the "
                        "notification area.",
                        "البرنامج يعمل بالفعل. ستجده في منطقة الإشعارات."),
    "quit_while_busy": ("A transcription is running. Stop it and exit?",
                        "يوجد تفريغ جارٍ. هل تريد إيقافه والخروج؟"),

    # ------------------------------------------------------------------- word
    "word_not_open": ("Word is not open. Open Word and a document, then try again.",
                      "برنامج وورد غير مفتوح. افتح وورد ومستندًا جديدًا ثم حاول "
                      "مرة أخرى."),
    "word_no_doc": ("No document is open in Word. Open a document, then try again.",
                    "لا يوجد مستند مفتوح في وورد. افتح مستندًا جديدًا ثم حاول "
                    "مرة أخرى."),
    "word_cannot_start": ("Word could not be started on this machine.",
                          "تعذّر تشغيل برنامج وورد على هذا الجهاز."),
    "word_no_access": ("The document open in Word could not be reached.",
                       "تعذّر الوصول إلى المستند المفتوح في وورد."),
    "word_insert_failed": ("The text could not be inserted into Word: {error}",
                           "تعذّر الإدراج في وورد: {error}"),
    "word_saved_instead": ("{message} The text is saved in a text file next to the "
                           "audio file.",
                           "{message} النص محفوظ في ملف نصي بجانب الملف الصوتي."),
    "undo_record": ("Insert transcript", "إدراج التفريغ النصي"),
    "job_no_model": ("The transcription model is not loaded.",
                     "نموذج التفريغ غير مُحمَّل."),
    "file_not_found": ("The selected file could not be found.",
                       "لم يُعثر على الملف المحدَّد."),

    # --------------------------------------------------------- reader channels
    "reader_jaws": ("JAWS", "JAWS"),
    "reader_nvda": ("NVDA", "NVDA"),
    "reader_sapi": ("Windows voice (SAPI)", "صوت ويندوز (SAPI)"),
    "reader_none": ("None - the windows are read by your screen reader directly",
                    "لا يوجد - الواجهة تُقرأ بقارئ الشاشة مباشرة"),
}


HELP_TEXT = {
    "en": """\
Sada - Transcription for Microsoft Word
How to use it

What it does
Turns an audio or video file into written text, and puts that text into the Word
document you already have open. It runs entirely on your machine: the audio is
never sent anywhere, it needs no internet connection once installed, and there is
no limit on the number of hours or the size of a file.

Using it in five steps
1. Open Word and a document.
2. Press the start-transcribing shortcut.
3. A file chooser opens. Pick the audio file and press Enter.
4. The program says which language it detected, then reports progress.
5. When it finishes, the text is inserted and the cursor is placed at the start of
   it, so you can begin reading straight away.

Dictating straight into Word
Press the dictation shortcut and start speaking. Each time you pause, that phrase
is written at the cursor and read back to you. Press the same shortcut again to
stop; whatever you were saying at that moment is still written.

Hesitation sounds - "um", "uh", "ممم" - are left out, and a held letter is
shortened, so "طيـــب" is written "طيب". Only sounds that are not words in any
language are removed: real words, including "يعني" and "اه", are always kept. You
can turn this off in Settings.

Dictation and transcribing a file cannot run at the same time, because they share
one model. Whichever is running, the program says so rather than interrupting it.

Each phrase is a separate undo step, so Ctrl+Z takes back the last thing you said
rather than the whole session.

The shortcuts
Start transcribing: opens the file chooser.
Dictation: starts speaking into the document, and stops it. One key for both.
Program window: status and every command in one place, including Exit.
Settings: opens the settings window.
Read the status: speaks what is happening, how far along it is, and the time left.
Cancel the current job: stops transcribing. Work already done is kept.
All shortcuts can be changed in Settings.

Notes for screen reader users
The text is inserted once at the end rather than a paragraph at a time, so the
reading cursor does not move while you are reading.
The whole insertion is a single undo: one Ctrl+Z removes all of it.
For Arabic the paragraph reading order is set right to left, so your screen reader
follows the correct order when you move by word.
The progress window never takes focus, neither when it appears nor when it updates.
If it talks too much, set "Speak progress every" to zero; you can still ask for the
status with its shortcut whenever you want.

Supported formats
Anything ffmpeg can read: mp3, m4a, wav, ogg, opus, wma, flac, aac, and video
files such as mp4, mkv and avi, whose audio is extracted automatically.

Expected time for one hour of audio
On an NVIDIA graphics card: two to eight minutes.
On the processor alone: eight to forty-five minutes, depending on the number of
cores and the model you chose.
The turbo model is about six times faster than large-v3 and the difference in
accuracy is small.

If something goes wrong
"Word is not open": open Word and a document, then try again.
Very slow: choose a smaller model in Settings, or close heavy programs.
Text that does not make sense: try the large-v3 model, or add the names and terms
to the "Names and terms" box in Settings.
Interrupted: the work done so far is saved, and transcribing the same file again
carries on from where it stopped.
""",
    "ar": """\
صدى - تفريغ صوتي داخل مايكروسوفت وورد
دليل الاستخدام

ما يفعله البرنامج
يحوّل ملفًا صوتيًا أو مرئيًا إلى نص مكتوب، ويضعه في مستند وورد المفتوح عندك.
يعمل كاملًا على جهازك: لا يُرسل الصوت إلى أي مكان، ولا يحتاج اتصالًا بالإنترنت
بعد التنصيب، ولا يوجد حد لعدد الساعات ولا لحجم الملف.

الاستخدام في خمس خطوات
١. افتح برنامج وورد ومستندًا جديدًا.
٢. اضغط اختصار بدء التفريغ.
٣. تظهر نافذة اختيار ملف. اختر الملف الصوتي واضغط إدخال.
٤. ينطق البرنامج اللغة التي كشفها ثم يُعلمك بالتقدّم.
٥. عند الانتهاء يُدرج النص في المستند، ويوضع مؤشر الكتابة في أول النص
   حتى تبدأ القراءة فورًا.

الإملاء مباشرةً في وورد
اضغط اختصار الإملاء وابدأ الكلام. كل مرة تسكت، تُكتب العبارة عند المؤشر وتُقرأ
عليك. اضغط الاختصار نفسه لتتوقف؛ وما كنت تقوله في تلك اللحظة يُكتب أيضًا.

تُحذف أصوات التردّد — «ممم» و«um» — ويُقصَّر الحرف الممدود، فتُكتب «طيـــب» هكذا:
«طيب». ولا يُحذف إلا ما ليس كلمةً في أي لغة: الكلمات الحقيقية، ومنها «يعني»
و«اه»، تبقى دائمًا. ويمكن إيقاف ذلك من الإعدادات.

لا يعمل الإملاء وتفريغ الملفات في وقت واحد، لأنهما يتشاركان نموذجًا واحدًا. وأيّهما
كان يعمل، يخبرك البرنامج بذلك بدلًا من مقاطعته.

كل عبارة خطوة تراجع مستقلة، فـ Ctrl+Z يسترجع آخر ما قلته لا الجلسة كلها.

الاختصارات
بدء التفريغ: يفتح نافذة اختيار الملف.
الإملاء: يبدأ الكلام في المستند ويوقفه. مفتاح واحد للاثنين.
نافذة البرنامج: الحالة وكل الأوامر في مكان واحد، ومنها الخروج.
الإعدادات: يفتح نافذة الإعدادات.
قراءة الحالة: ينطق ما يجري الآن ونسبة التقدّم والوقت المتوقع.
إلغاء العمل الجاري: يوقف التفريغ. ما تم إنجازه يبقى محفوظًا.
كل الاختصارات قابلة للتغيير من الإعدادات.

ملاحظات تهمّ مستخدمي قارئ الشاشة
يُدرج النص مرة واحدة في النهاية، لا تدريجيًا، حتى لا يتحرك مؤشر القراءة
أثناء قراءتك.
الإدراج كله عملية تراجع واحدة: ضغطة Ctrl+Z واحدة تُلغي النص كله.
للنص العربي يُضبط اتجاه الفقرة من اليمين لليسار، فيتبع قارئ الشاشة
ترتيب القراءة الصحيح عند التنقل بالكلمة.
نافذة التقدّم لا تخطف التركيز أبدًا، لا عند ظهورها ولا عند التحديث.
إن أردت تقليل الكلام أثناء العمل، اجعل «نطق التقدّم كل» صفرًا، وستبقى
قادرًا على سؤال البرنامج عن حالته باختصار قراءة الحالة.

الصيغ المدعومة
أي صيغة يقرأها ffmpeg: mp3، m4a، wav، ogg، opus، wma، flac، aac،
وكذلك ملفات الفيديو mp4 و mkv و avi، إذ يُستخرج منها الصوت تلقائيًا.

الوقت المتوقع لساعة صوت
على كارت رسوم NVIDIA: من دقيقتين إلى ثماني دقائق.
على المعالج فقط: من ثماني دقائق إلى خمس وأربعين دقيقة، حسب عدد الأنوية
والنموذج المختار.
النموذج turbo أسرع بنحو ست مرات من large-v3 وفرق الدقة بينهما صغير.

إذا حدثت مشكلة
«برنامج وورد غير مفتوح»: افتح وورد ومستندًا، ثم أعد المحاولة.
التفريغ بطيء جدًا: اختر نموذجًا أصغر من الإعدادات، أو أغلق البرامج الثقيلة.
نص غير مفهوم: جرّب نموذج large-v3، أو أضف الأسماء والمصطلحات في خانة
«مصطلحات وأسماء» بالإعدادات.
انقطع العمل: ما تم إنجازه محفوظ، وإعادة تشغيل التفريغ على الملف نفسه
تُكمل من حيث توقّف.
""",
}

ABOUT_TEXT = {
    "en": """\
Sada
Transcription for Microsoft Word

Turns audio and video files into written text inside Microsoft Word.

It runs entirely on your machine. The audio is never sent to any service, no
internet connection is needed while it works, no service key is required, and
there is no limit on hours or file size.

Speech recognition model: Whisper large-v3-turbo from OpenAI.
It detects the language automatically and supports more than ninety languages.

Designed to be fully usable from the keyboard and with the NVDA and JAWS screen
readers.

Created by Mohammed Khaled.
""",
    "ar": """\
صدى
تفريغ صوتي داخل مايكروسوفت وورد

يحوّل الملفات الصوتية والمرئية إلى نص مكتوب داخل برنامج وورد.

يعمل كاملًا على جهازك. لا يُرسل الصوت إلى أي خدمة، ولا يحتاج اتصالًا
بالإنترنت أثناء العمل، ولا يحتاج مفتاح خدمة، ولا يوجد حد لعدد الساعات
أو لحجم الملف.

نموذج التعرّف على الكلام: Whisper large-v3-turbo من OpenAI.
يتعرّف على اللغة تلقائيًا ويدعم أكثر من تسعين لغة.

صُمّم ليكون قابلًا للاستخدام بالكامل من لوحة المفاتيح ومع قارئات
الشاشة NVDA و JAWS.

تطوير: محمد خالد.
""",
}


def help_text() -> str:
    return HELP_TEXT.get(_current, HELP_TEXT["en"])


def about_text() -> str:
    return ABOUT_TEXT.get(_current, ABOUT_TEXT["en"])
