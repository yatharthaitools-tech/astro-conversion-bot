"""Admin dashboard routes — conversations list/detail + analytics.

Scoped deliberately narrow: astrohelp's admin-app also has ticket-queue,
Slack/email mock logs, Sheets sync, and a multi-admin roster — those are
astrologer-support-specific (KAM/CS routing, human ops tooling) and don't
have a real counterpart in this bot today. This covers what's actually
useful here: seeing what visitors are saying and how the agent is
handling it.
"""
from flask import Blueprint, redirect, render_template, request, url_for

from dashboard import auth, db, health
from services import ticket_service

bp = Blueprint('dashboard', __name__, url_prefix='/admin', template_folder='templates')

PAGE_SIZE = 25


@bp.context_processor
def inject_health():
    # Only actually runs the check when an admin page is being rendered
    # (not on every /ask), and only once logged in — no point surfacing
    # this to an unauthenticated visitor hitting /admin/login.
    if auth.is_logged_in():
        return {'system_health': health.check()}
    return {'system_health': None}


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if auth.is_logged_in():
        return redirect(url_for('dashboard.conversations'))

    error = None
    if not auth.is_configured():
        error = ('Google sign-in is not configured on the server — set GOOGLE_OAUTH_CLIENT_ID and '
                 'ADMIN_ALLOWED_EMAILS or ADMIN_ALLOWED_DOMAIN. The dashboard is disabled until then.')
    elif request.method == 'POST':
        # Google's button POSTs here with the ID token as `credential`, plus
        # the same g_csrf_token in both a cookie and the form body
        # (double-submit CSRF check, per Google's docs).
        csrf_cookie = request.cookies.get('g_csrf_token')
        if not csrf_cookie or csrf_cookie != request.form.get('g_csrf_token'):
            error = 'Sign-in failed (CSRF check). Please try again.'
        else:
            email = auth.verify_google_credential(request.form.get('credential', ''))
            if email:
                auth.log_in(email)
                return redirect(url_for('dashboard.conversations'))
            error = 'That Google account is not allowed to access the admin dashboard.'
    return render_template('login.html', error=error,
                           google_client_id=auth.GOOGLE_OAUTH_CLIENT_ID,
                           configured=auth.is_configured())


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


@bp.route('/tickets')
@auth.admin_required
def tickets():
    page = max(1, request.args.get('page', 1, type=int))
    status = request.args.get('status') or None
    category = request.args.get('category') or None
    date_from = request.args.get('from') or None
    date_to = request.args.get('to') or None

    rows = db.list_tickets(
        limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE,
        status=status, category=category, date_from=date_from, date_to=date_to,
    )
    total = db.count_tickets(status=status, category=category, date_from=date_from, date_to=date_to)

    return render_template(
        'tickets.html',
        tickets=rows,
        page=page,
        total_pages=max(1, -(-total // PAGE_SIZE)),
        total=total,
        statuses=db.TICKET_STATUSES,
        filters={'status': status or '', 'category': category or '', 'from': date_from or '', 'to': date_to or ''},
        show_nav=True, active='tickets',
    )


@bp.route('/tickets/<int:ticket_id>', methods=['GET', 'POST'])
@auth.admin_required
def ticket_detail(ticket_id):
    if request.method == 'POST':
        new_status = request.form.get('status')
        note = request.form.get('note') or None
        if new_status in db.TICKET_STATUSES:
            ticket_service.update_ticket_status(ticket_id, new_status, note)
        return redirect(url_for('dashboard.ticket_detail', ticket_id=ticket_id))

    ticket = db.get_ticket(ticket_id)
    if not ticket:
        return render_template(
            'ticket_detail.html', ticket=None, ticket_id=ticket_id,
            statuses=db.TICKET_STATUSES, show_nav=True, active='tickets',
        ), 404
    return render_template(
        'ticket_detail.html', ticket=ticket, ticket_id=ticket_id,
        statuses=db.TICKET_STATUSES, show_nav=True, active='tickets',
    )
