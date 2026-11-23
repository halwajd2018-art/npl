"""One-time administrator bootstrap. Run from project root."""
from app.main import bootstrap_db,create_admin
from getpass import getpass
if __name__=='__main__':
    bootstrap_db()
    print('إعداد حساب مدير منصة التقارير (كلمة المرور لا تظهر أثناء الكتابة)')
    username=input('اسم المستخدم (لاتيني): ').strip()
    display=input('الاسم الظاهر: ').strip() or 'مدير الموقع'
    password=getpass('كلمة المرور (12 حرفاً فأكثر): ')
    create_admin(username,password,display)
    print('تم إنشاء مدير الموقع. شغل الخادم الآن.')
