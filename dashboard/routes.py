"""Admin dashboard routes — conversations list/detail + analytics.

Scoped deliberately narrow: astrohelp's admin-app also has ticket-queue,
Slack/email mock logs, Sheets sync, and a multi-admin roster — those are
astrologer-support-specific (KAM/CS routing, human ops tooling) and don't
have a real counterpart in this bot today. This covers what's actually
useful here: seeing what visitors are saying and how the agent is
handling it.
"""
from flask import Blueprint, redirect, render_template, request, url_for

from dashboard import auth, db

bp = Blueprint('dashboard', __name__, url_prefix='/admin', template_folder='templates')

PAGE_SIZE = 25


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if auth.is_logged_in():
        return redirect(url_for('dashboard.conversations'))

    error = None
    if request.method == 'POST':
        if not auth.is_configured():
            error = 'ADMIN_PASSWORD is not set on the server — the dashboard is disabled until it is.'
        elif auth.check_password(request.form.get('password', '')):
            auth.log_in()
            return redirect(url_for('dashboard.conversations'))
        else:
            error = 'Wrong password.'
    return render_template('login.html', error=error)


@bp.route('/logout', methods=['POST'])
def logout():
    auth.log_out()
    return redirect(url_for('dashboard.login'))


@bp.route('/')
@auth.admin_required
def index():
    return redirect(url_for('dashboard.conversations'))


@bp.route('/conversations')
@auth.admin_required
def conversations():
    page = max(1, request.args.get('page', 1, type=int))
    language = request.args.get('language') or None
    date_from = request.args.get('from') or None
    date_to = request.args.get('to') or None

    rows = db.list_conversations(
        limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE,
        language=language, date_from=date_from, date_to=date_to,
    )
    total = db.count_conversations(language=language, date_from=date_from, date_to=date_to)

    return render_template(
        'conversations.html',
        conversations=rows,
        page=page,
        total_pages=max(1, -(-total // PAGE_SIZE)),
        total=total,
        filters={'language': language or '', 'from': date_from or '', 'to': date_to or ''},
        show_nav=True, active='conversations',
    )


@bp.route('/conversations/<session_id>')
@auth.admin_required
def conversation_detail(session_id):
    conv = db.get_conversation(session_id)
    if not conv:
        return render_template(
            'conversation_detail.html', conversation=None, session_id=session_id,
            show_nav=True, active='conversations',
        ), 404
    return render_template(
        'conversation_detail.html', conversation=conv, session_id=session_id,
        show_nav=True, active='conversations',
    )


@bp.route('/analytics')
@auth.admin_required
def analytics():
    date_from = request.args.get('from') or None
    date_to = request.args.get('to') or None
    stats = db.get_analytics(date_from=date_from, date_to=date_to)
    return render_template(
        'analytics.html', stats=stats, filters={'from': date_from or '', 'to': date_to or ''},
        show_nav=True, active='analytics',
    )
