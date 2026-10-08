"""Admin dashboard routes — conversations list/detail, tickets (with
round-robin assignment), analytics, and per-person user/role management.

Scoped deliberately narrow: astrohelp's admin-app also has Slack/email mock
logs and Sheets sync — those are astrologer-support-specific (KAM/CS
routing, human ops tooling) and don't have a real counterpart in this bot
today. This covers what's actually useful here: seeing what visitors are
saying, working their tickets, and (now) who's logged in to do that.

Role gating (see dashboard/auth.py's ROLES): 'admin' can do everything
including manage users; 'support_agent' handles Conversations + Tickets,
including updating status/assignment, but not Analytics or Users;
'analyst' gets Analytics plus read-only Conversations/Tickets — enforced
both here (role_required / inline checks) and in the templates (so a
role that can't act doesn't even see the control).
"""
import csv
import io

from flask import Blueprint, Response, redirect, render_template, request, url_for

from dashboard import auth, db, health
from services import ticket_service

bp = Blueprint('dashboard', __name__, url_prefix='/admin', template_folder='templates')

PAGE_SIZE = 25


@bp.context_processor
def inject_context():
    # Only actually runs the health check when an admin page is being
    # rendered (not on every /ask), and only once logged in — no point
    # surfacing this to an unauthenticated visitor hitting /admin/login.
    user = auth.current_user()
    return {
        'system_health': health.check() if user else None,
        'current_user': user,
    }


def _login_template_context(error=None):
    return {
        'error': error,
        'google_signin_configured': auth.google_signin_configured(),
        'google_client_id': auth.GOOGLE_CLIENT_ID,
        'google_login_uri': url_for('dashboard.login_google', _external=True),
    }


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if auth.is_logged_in():
        return redirect(url_for('dashboard.conversations'))

    error = None
    if request.method == 'POST':
        user = auth.authenticate(request.form.get('email', ''), request.form.get('password', ''))
        if user:
            auth.log_in(user)
            return redirect(url_for('dashboard.conversations'))
        error = 'Wrong email or password.'
    return render_template('login.html', **_login_template_context(error))


@bp.route('/login/google', methods=['POST'])
def login_google():
    if auth.is_logged_in():
        return redirect(url_for('dashboard.conversations'))

    # Google's own CSRF protection for the login_uri POST flow: it sets a
    # g_csrf_token cookie and includes the same value as a form field: a
    # cross-site form targeting this endpoint can't read/replay OUR
    # cookie, so a mismatch (or either one missing) means this POST
    # didn't genuinely come from the sign-in button on our own page.
    cookie_token = request.cookies.get('g_csrf_token')
    body_token = request.form.get('g_csrf_token')
    if not cookie_token or not body_token or cookie_token != body_token:
        return render_template('login.html', **_login_template_context(
            'Google sign-in request could not be verified. Please try again.',
        )), 400

    user, error = auth.authenticate_google(request.form.get('credential', ''))
    if user:
        auth.log_in(user)
        return redirect(url_for('dashboard.conversations'))
    return render_template('login.html', **_login_template_context(error))


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
@auth.role_required('admin', 'analyst')
def analytics():
    date_from = request.args.get('from') or None
    date_to = request.args.get('to') or None
    stats = db.get_analytics(date_from=date_from, date_to=date_to)
    events = db.get_event_analytics()
    return render_template(
        'analytics.html', stats=stats, events=events, filters={'from': date_from or '', 'to': date_to or ''},
        show_nav=True, active='analytics',
    )


def _ticket_filters_from_request():
    return {
        'status': request.args.get('status') or None,
        'category': request.args.get('category') or None,
        'date_from': request.args.get('from') or None,
        'date_to': request.args.get('to') or None,
        'assigned_admin_id': request.args.get('assigned') or None,
        'language': request.args.get('language') or None,
    }


@bp.route('/tickets')
@auth.admin_required
def tickets():
    page = max(1, request.args.get('page', 1, type=int))
    filters = _ticket_filters_from_request()

    rows = db.list_tickets(limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, **filters)
    total = db.count_tickets(**filters)

    return render_template(
        'tickets.html',
        tickets=rows,
        page=page,
        total_pages=max(1, -(-total // PAGE_SIZE)),
        total=total,
        statuses=db.TICKET_STATUSES,
        assignable_admins=db.list_assignable_admins(),
        language_codes=db.LANGUAGE_CODES,
        filters={
            'status': filters['status'] or '', 'category': filters['category'] or '',
            'from': filters['date_from'] or '', 'to': filters['date_to'] or '',
            'assigned': filters['assigned_admin_id'] or '', 'language': filters['language'] or '',
        },
        show_nav=True, active='tickets',
    )


@bp.route('/tickets/bulk-reassign', methods=['POST'])
@auth.role_required('admin')
def bulk_reassign_tickets():
    """#4: reassign everything off/onto someone in one shot — e.g. an
    agent going on leave. Admin-only (unlike the per-ticket reassign
    dropdown, which Support Agents can also use) since this can move a
    large chunk of the queue at once."""
    ticket_ids = [int(t) for t in request.form.getlist('ticket_ids') if t.isdigit()]
    raw_admin_id = request.form.get('assigned_admin_id') or ''
    admin_id = int(raw_admin_id) if raw_admin_id else None
    if ticket_ids:
        db.bulk_update_ticket_assignee(ticket_ids, admin_id)
    # The tickets page embeds its current filters as hidden fields in this
    # same form (see tickets.html) so a bulk reassign lands back on the
    # same filtered view instead of resetting to an unfiltered list.
    redirect_args = {
        k: request.form.get(k) for k in ('status', 'category', 'from', 'to', 'assigned', 'language', 'page')
        if request.form.get(k)
    }
    return redirect(url_for('dashboard.tickets', **redirect_args))


@bp.route('/tickets/export.csv')
@auth.admin_required
def export_tickets_csv():
    """#7 — exports whatever the current filters show, same filter logic
    as the tickets() list view itself (not a separate unfiltered dump)."""
    filters = _ticket_filters_from_request()
    rows = db.list_tickets(limit=100000, offset=0, **filters)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        'Ticket', 'User', 'Category', 'Sub-category', 'Description', 'Status',
        'Language', 'Assigned To', 'LTV Tier', 'Created', 'Resolved',
    ])
    for t in rows:
        writer.writerow([
            t['ticket_ref'], t['user_id'], t['category'], t.get('sub_category') or '',
            t.get('description') or '', t['status'], t.get('language') or '',
            t.get('assigned_admin_email') or '', t.get('ltv_tier') or '',
            t['created_at'], t.get('resolved_at') or '',
        ])
    return Response(
        buffer.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=tickets.csv'},
    )


@bp.route('/conversations/export.csv')
@auth.admin_required
def export_conversations_csv():
    language = request.args.get('language') or None
    date_from = request.args.get('from') or None
    date_to = request.args.get('to') or None
    rows = db.list_conversations(limit=100000, offset=0, language=language, date_from=date_from, date_to=date_to)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        'Session', 'User', 'Language', 'Turns', 'Resolved By', 'Rating', 'First Seen', 'Last Seen',
    ])
    for c in rows:
        writer.writerow([
            c['session_id'], c['user_id'], c.get('last_language') or '', c['turn_count'],
            c.get('resolved_by') or '', c.get('rating') if c.get('rating') is not None else '',
            c['first_seen_at'], c['last_seen_at'],
        ])
    return Response(
        buffer.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=conversations.csv'},
    )


@bp.route('/tickets/<int:ticket_id>', methods=['GET', 'POST'])
@auth.admin_required
def ticket_detail(ticket_id):
    if request.method == 'POST':
        # Analyst is read-only here — the form isn't rendered for them,
        # but a POST could still be sent directly, so enforce it too.
        if auth.current_user()['role'] not in ('admin', 'support_agent'):
            return render_template('403.html', show_nav=True), 403

        if 'status' in request.form:
            new_status = request.form.get('status')
            note = request.form.get('note') or None
            if new_status in db.TICKET_STATUSES:
                ticket_service.update_ticket_status(ticket_id, new_status, note)
        elif 'assigned_admin_id' in request.form:
            raw = request.form.get('assigned_admin_id') or ''
            ticket_service.update_ticket_assignee(ticket_id, int(raw) if raw else None)
        elif 'reply' in request.form:
            reply_text = request.form.get('reply') or ''
            author_name = auth.current_user().get('email')
            ticket_service.send_agent_reply(ticket_id, reply_text, author_name=author_name)
        return redirect(url_for('dashboard.ticket_detail', ticket_id=ticket_id))

    ticket = db.get_ticket(ticket_id)
    if not ticket:
        return render_template(
            'ticket_detail.html', ticket=None, ticket_id=ticket_id,
            statuses=db.TICKET_STATUSES, assignable_admins=[], language_codes=db.LANGUAGE_CODES,
            show_nav=True, active='tickets',
        ), 404
    # Past replies sent to this ticket's visitor — same query the chat
    # widget's own poll uses (db.get_new_agent_messages with no 'since'
    # returns the full backlog), so the dashboard shows exactly what the
    # visitor has seen, regardless of whether a reply came from Zoho or
    # was typed here.
    replies = db.get_new_agent_messages(ticket['session_id'], None) if ticket.get('session_id') else []
    return render_template(
        'ticket_detail.html', ticket=ticket, ticket_id=ticket_id, replies=replies,
        statuses=db.TICKET_STATUSES, assignable_admins=db.list_assignable_admins(),
        language_codes=db.LANGUAGE_CODES, show_nav=True, active='tickets',
    )


# --- Users (admin-only) --------------------------------------------------

@bp.route('/users', methods=['GET', 'POST'])
@auth.role_required('admin')
def users():
    error = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        role = request.form.get('role') or ''
        if not email or not password or role not in auth.ROLES:
            error = 'Email, password and a valid role are all required.'
        elif len(password) < 8:
            error = 'Password must be at least 8 characters.'
        else:
            languages = [l for l in request.form.getlist('languages') if l in db.LANGUAGE_CODES]
            created = db.create_admin_user(email, auth.hash_password(password), role, languages)
            if not created:
                error = f'{email} already has an account.'
            else:
                return redirect(url_for('dashboard.users'))

    return render_template(
        'users.html', users=db.list_admin_users(), roles=auth.ROLES,
        role_labels=auth.ROLE_LABELS, language_codes=db.LANGUAGE_CODES,
        error=error, show_nav=True, active='users',
    )


@bp.route('/users/<int:user_id>/role', methods=['POST'])
@auth.role_required('admin')
def update_user_role(user_id):
    new_role = request.form.get('role')
    if new_role in auth.ROLES:
        # Never leave the dashboard with zero admins — nobody left could
        # undo this or manage users at all.
        target = db.get_admin_user_by_id(user_id)
        if target and target['role'] == 'admin' and new_role != 'admin' and db.count_admin_users('admin') <= 1:
            return render_template('403.html', show_nav=True,
                                    message="Can't demote the last remaining Admin."), 403
        db.update_admin_user_role(user_id, new_role)
    return redirect(url_for('dashboard.users'))


@bp.route('/users/<int:user_id>/languages', methods=['POST'])
@auth.role_required('admin')
def update_user_languages(user_id):
    """#4: which of Hindi/Tamil/Telugu/Malayalam this person handles —
    round-robin prefers them for a matching ticket's language. Empty
    selection is valid (clears their languages, meaning no preference,
    not 'handles nothing' — see _pick_round_robin_assignee)."""
    languages = [l for l in request.form.getlist('languages') if l in db.LANGUAGE_CODES]
    db.update_admin_user_languages(user_id, languages)
    return redirect(url_for('dashboard.users'))


@bp.route('/users/<int:user_id>/delete', methods=['POST'])
@auth.role_required('admin')
def delete_user(user_id):
    current = auth.current_user()
    if current['id'] == user_id:
        return render_template('403.html', show_nav=True,
                                message="Can't delete your own account while logged in as it."), 403
    target = db.get_admin_user_by_id(user_id)
    if target and target['role'] == 'admin' and db.count_admin_users('admin') <= 1:
        return render_template('403.html', show_nav=True,
                                message="Can't delete the last remaining Admin."), 403
    db.delete_admin_user(user_id)
    return redirect(url_for('dashboard.users'))
