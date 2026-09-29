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
from flask import Blueprint, redirect, render_template, request, url_for

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


@bp.route('/tickets')
@auth.admin_required
def tickets():
    page = max(1, request.args.get('page', 1, type=int))
    status = request.args.get('status') or None
    category = request.args.get('category') or None
    date_from = request.args.get('from') or None
    date_to = request.args.get('to') or None
    assigned = request.args.get('assigned') or None

    rows = db.list_tickets(
        limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE,
        status=status, category=category, date_from=date_from, date_to=date_to,
        assigned_admin_id=assigned,
    )
    total = db.count_tickets(
        status=status, category=category, date_from=date_from, date_to=date_to,
        assigned_admin_id=assigned,
    )

    return render_template(
        'tickets.html',
        tickets=rows,
        page=page,
        total_pages=max(1, -(-total // PAGE_SIZE)),
        total=total,
        statuses=db.TICKET_STATUSES,
        assignable_admins=db.list_assignable_admins(),
        filters={
            'status': status or '', 'category': category or '',
            'from': date_from or '', 'to': date_to or '', 'assigned': assigned or '',
        },
        show_nav=True, active='tickets',
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
        return redirect(url_for('dashboard.ticket_detail', ticket_id=ticket_id))

    ticket = db.get_ticket(ticket_id)
    if not ticket:
        return render_template(
            'ticket_detail.html', ticket=None, ticket_id=ticket_id,
            statuses=db.TICKET_STATUSES, assignable_admins=[], show_nav=True, active='tickets',
        ), 404
    return render_template(
        'ticket_detail.html', ticket=ticket, ticket_id=ticket_id,
        statuses=db.TICKET_STATUSES, assignable_admins=db.list_assignable_admins(),
        show_nav=True, active='tickets',
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
            created = db.create_admin_user(email, auth.hash_password(password), role)
            if not created:
                error = f'{email} already has an account.'
            else:
                return redirect(url_for('dashboard.users'))

    return render_template(
        'users.html', users=db.list_admin_users(), roles=auth.ROLES,
        role_labels=auth.ROLE_LABELS, error=error, show_nav=True, active='users',
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
