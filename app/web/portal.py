"""Portal do parceiro (Telas 1 a 6 da Especificação) e páginas comuns."""
from __future__ import annotations

import os
from decimal import Decimal

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for)

from .. import cnpj as cn
from .. import security as sec
from .. import services as svc
from ..core import db, now
from ..models import (TERM_KEYS, CommissionLine, Material, Notification, Partner, PartnerInvoice, Referral,
                      ReviewRequest, SurveyResponse, TermAcceptance, TermDocument)
from ..rules import active_ruleset, recruit_cap_for_year
from .helpers import LINE_HELP, LINE_TONE, fail, ok, parse_date, parse_money, referral_status, track

bp = Blueprint("portal", __name__)


@bp.app_template_global()
def ref_status(ref):
    return referral_status(ref)


@bp.app_template_global()
def ref_track(ref):
    return track(ref)


@bp.route("/")
@sec.login_required
def home():
    u = sec.current_user()
    if u.is_staff:
        return redirect(url_for("manage.painel"))
    return redirect(url_for("portal.painel"))


# ------------------------------------------------------------------ entrada em andamento
@bp.route("/aguardando")
@sec.login_required
def onboarding():
    p = sec.current_user().partner
    if not p:
        return redirect(url_for("manage.painel"))
    if p.status == "ativo":
        return redirect(url_for("portal.painel"))
    docs = TermDocument.query.filter(TermDocument.key.in_(list(TERM_KEYS))).order_by(TermDocument.key).all()
    accepted = {(a.term_key, a.version) for a in TermAcceptance.query.filter_by(partner_id=p.id)}
    return render_template("aguardando.html", p=p, docs=docs, accepted=accepted)


@bp.route("/aguardando/termos/<key>", methods=["POST"])
@sec.login_required
def aceitar_termo(key):
    p = sec.current_user().partner
    if not p:
        abort(403)
    try:
        svc.accept_term(p, key, request.remote_addr)
        ok("Termo aceito.")
    except svc.ServiceError as e:
        fail(e)
    return redirect(url_for("portal.onboarding"))


# ------------------------------------------------------------------ Tela 1: painel
@bp.route("/painel")
@sec.active_partner_required
def painel():
    p = g.partner
    lines = CommissionLine.query.filter_by(beneficiary_id=p.id).all()
    sums = {s: sum((l.amount for l in lines if l.state == s), Decimal("0")) for s in
            ("aguardando_condicao", "aprovada", "programada", "paga")}
    proj = svc.projected_lines(p)
    est = sum((x["amount"] for x in proj), Decimal("0"))
    refs = Referral.query.filter_by(partner_id=p.id).order_by(Referral.created_at.desc()).all()
    andamento = [r for r in refs if r.eligibility in ("recebida", "em_validacao")
                 or (r.eligibility == "aceita" and r.stage not in ("ganha", "perdida"))]
    pendencias = []
    n_nf = sum(1 for l in lines if l.state == "aprovada")
    if n_nf:
        pendencias.append((f"{n_nf} comissão(ões) aprovada(s) aguardam sua nota fiscal.", url_for("portal.comissoes")))
    for b in svc.receiving_block_reasons(p):
        pendencias.append((b, url_for("portal.cadastro")))
    for rr in ReviewRequest.query.filter_by(partner_id=p.id, status="resolvido").order_by(ReviewRequest.resolved_at.desc()).limit(2):
        pendencias.append((f"Resposta ao seu pedido de revisão: {rr.resolution}", url_for("portal.cadastro")))
    open_, reason = svc.recruit_link_state(p)
    return render_template("painel.html", p=p, sums=sums, est=est, andamento=andamento, pendencias=pendencias,
                           n_refs=len(refs), recruit_open=open_)


# ------------------------------------------------------------------ Tela 2: indicações
@bp.route("/indicacoes")
@sec.active_partner_required
def indicacoes():
    refs = Referral.query.filter_by(partner_id=g.partner.id).order_by(Referral.created_at.desc()).all()
    return render_template("indicacoes.html", refs=refs)


@bp.route("/indicacoes/nova", methods=["GET", "POST"])
@sec.active_partner_required
def indicacao_nova():
    p = g.partner
    if request.method == "POST":
        step = request.form.get("step")
        raw = request.form.get("cnpj", "")
        if step == "check":
            try:
                svc.check_availability(p, raw)
                return render_template("indicacao_nova.html", stage="form", cnpj=cn.fmt(raw), form={})
            except svc.Unavailable as e:
                return render_template("indicacao_nova.html", stage="check", unavailable=str(e), cnpj=raw)
            except svc.ServiceError as e:
                fail(e)
                return render_template("indicacao_nova.html", stage="check", cnpj=raw)
        try:
            ref = svc.register_referral(p, request.form.to_dict())
            rules = active_ruleset().rules
            ok(f"Indicação registrada com o protocolo {ref.protocol}. A validação leva até {rules['sla_review_days']} dias úteis.")
            return redirect(url_for("portal.indicacao_detalhe", rid=ref.id))
        except svc.Unavailable as e:
            return render_template("indicacao_nova.html", stage="check", unavailable=str(e), cnpj=raw)
        except svc.ServiceError as e:
            fail(e)
            return render_template("indicacao_nova.html", stage="form", cnpj=raw, form=request.form)
    return render_template("indicacao_nova.html", stage="check", cnpj="")


@bp.route("/indicacoes/<int:rid>")
@sec.active_partner_required
def indicacao_detalhe(rid):
    ref = Referral.query.filter_by(id=rid, partner_id=g.partner.id).first_or_404()
    return render_template("indicacao_detalhe.html", ref=ref)


@bp.route("/revisao", methods=["GET", "POST"])
@sec.active_partner_required
def revisao():
    ref = None
    if request.args.get("indicacao") or request.form.get("indicacao"):
        rid = request.values["indicacao"]
        if not rid.isdigit():
            abort(404)
        ref = Referral.query.filter_by(id=int(rid), partner_id=g.partner.id).first_or_404()
    if request.method == "POST":
        try:
            svc.request_review(g.partner, request.form.get("message", ""), ref, request.form.get("cnpj"))
            ok("Pedido de revisão enviado. A All Targets responde por aqui.")
            return redirect(url_for("portal.cadastro"))
        except svc.ServiceError as e:
            fail(e)
    return render_template("revisao.html", ref=ref, cnpj=request.values.get("cnpj", ""))


# ------------------------------------------------------------------ Tela 3: convidar parceiro
@bp.route("/parceiros")
@sec.active_partner_required
def parceiros():
    p = g.partner
    rules = active_ruleset().rules
    open_, reason = svc.recruit_link_state(p)
    year = now().year
    recruited = Partner.query.filter_by(invited_by_id=p.id).order_by(Partner.application_at).all()
    rows = []
    for r in recruited:
        first_ref = Referral.query.filter(Referral.partner_id == r.id, Referral.accepted_at.isnot(None)).order_by(Referral.accepted_at).first()
        has_contract = any(not c.cancelled_at for ref in Referral.query.filter_by(partner_id=r.id).all() for c in ref.contracts)
        marcos = [("Candidatura enviada", True), ("Aprovado", bool(r.approved_at)), ("Workshop concluído", bool(r.workshop_at)),
                  ("Acesso liberado", r.status == "ativo"), ("Primeira indicação aceita", bool(first_ref)),
                  ("Primeiro contrato assinado", has_contract)]
        rows.append((r, marcos))
    link = f"{current_app.config['PUBLIC_BASE_URL']}/convite/{p.invite_token}"
    return render_template("parceiros.html", p=p, open_=open_, reason=reason, link=link, rows=rows,
                           used=svc.recruits_of(p, year), cap=recruit_cap_for_year(rules, year), year=year)


# ------------------------------------------------------------------ Tela 4: comissões e pagamentos
@bp.route("/comissoes")
@sec.active_partner_required
def comissoes():
    p = g.partner
    lines = CommissionLine.query.filter_by(beneficiary_id=p.id).order_by(CommissionLine.created_at.desc()).all()
    proj = svc.projected_lines(p)
    invoices = PartnerInvoice.query.filter_by(partner_id=p.id).order_by(PartnerInvoice.uploaded_at.desc()).all()
    aprovadas = [l for l in lines if l.state == "aprovada"]
    rules = active_ruleset().rules
    return render_template("comissoes.html", lines=lines, proj=proj, invoices=invoices, aprovadas=aprovadas,
                           blocks=svc.receiving_block_reasons(p), tone=LINE_TONE, help=LINE_HELP,
                           deadline=rules["nf_deadline_day"], p=p)


@bp.route("/comissoes/nf", methods=["POST"])
@sec.active_partner_required
def enviar_nf():
    f = request.form
    try:
        ids = [int(x) for x in f.getlist("line_ids")]
        inv = svc.attach_invoice(current_app.config, g.partner, f.get("number", "").strip(), parse_date(f.get("issue_date"), "data de emissão"),
                                 parse_money(f.get("value")), request.files.get("file"), ids)
        ok(f"Nota fiscal {inv.number} enviada. Pagamento previsto para o ciclo de {svc_cycle(ids)}, depois da conferência.")
    except (svc.ServiceError, ValueError) as e:
        fail(e)
    return redirect(url_for("portal.comissoes"))


def svc_cycle(ids):
    l = db.session.get(CommissionLine, ids[0]) if ids else None
    return f"{l.cycle:%m/%Y}" if l and l.cycle else "próximo mês"


@bp.route("/nf/<int:iid>")
@sec.login_required
def baixar_nf(iid):
    inv = db.session.get(PartnerInvoice, iid) or abort(404)
    u = sec.current_user()
    if not (u.has_role("finance", "manager") or (u.partner_id == inv.partner_id)):
        abort(403)
    if not inv.file_path or not os.path.exists(inv.file_path):
        abort(404)
    return send_file(inv.file_path, as_attachment=True)


# ------------------------------------------------------------------ Tela 5: materiais e regras
@bp.route("/materiais")
@sec.active_partner_required
def materiais():
    rules = active_ruleset().rules
    mats = Material.query.filter_by(published=True).order_by(Material.position, Material.id).all()
    year = now().year
    accepted = TermAcceptance.query.filter_by(partner_id=g.partner.id).all()
    docs = {d.key: d for d in TermDocument.query.all()}
    return render_template("materiais.html", rules=rules, mats=mats, cap=recruit_cap_for_year(rules, year), year=year,
                           accepted=accepted, docs=docs)


# ------------------------------------------------------------------ Tela 6: cadastro e suporte
@bp.route("/cadastro", methods=["GET", "POST"])
@sec.active_partner_required
def cadastro():
    p = g.partner
    if request.method == "POST":
        f = request.form
        p.contact_name, p.phone = f.get("contact_name", p.contact_name), f.get("phone")
        p.city, p.uf = f.get("city"), (f.get("uf") or "").upper()[:2] or None
        bank = (f.get("bank"), f.get("bank_agency"), f.get("bank_account"), cn.digits(f.get("bank_holder_doc", "")))
        if bank != (p.bank, p.bank_agency, p.bank_account, p.bank_holder_doc):
            p.bank, p.bank_agency, p.bank_account, p.bank_holder_doc = bank
            p.bank_verified = False
            svc.notify("dados_bancarios", f"{p.display_name} alterou os dados bancários. Conferir a titularidade.", staff=True)
            svc.audit(p.email, "partner.bank_update", "partner", p.id)
        db.session.commit()
        ok("Cadastro atualizado.")
        return redirect(url_for("portal.cadastro"))
    reviews = ReviewRequest.query.filter_by(partner_id=p.id).order_by(ReviewRequest.created_at.desc()).all()
    return render_template("cadastro.html", p=p, blocks=svc.receiving_block_reasons(p), reviews=reviews,
                           answered=SurveyResponse.query.filter_by(partner_id=p.id).first())


@bp.route("/cadastro/pesquisa", methods=["POST"])
@sec.active_partner_required
def pesquisa():
    f = request.form
    try:
        score = int(f.get("score", ""))
        if not 0 <= score <= 10:
            raise ValueError
    except ValueError:
        flash("Escolha uma nota de 0 a 10.", "error")
        return redirect(url_for("portal.cadastro"))
    db.session.add(SurveyResponse(partner_id=g.partner.id, score=score, calc_doubt=bool(f.get("calc_doubt")), comment=f.get("comment")))
    db.session.commit()
    ok("Obrigado pela avaliação.")
    return redirect(url_for("portal.cadastro"))


# ------------------------------------------------------------------ avisos
@bp.route("/avisos")
@sec.login_required
def notificacoes():
    u = sec.current_user()
    items = Notification.query.filter_by(user_id=u.id).order_by(Notification.created_at.desc()).limit(60).all()
    unread_ids = [n.id for n in items if n.read_at is None]
    html = render_template("avisos.html", items=items, unread_ids=set(unread_ids))
    if unread_ids:
        Notification.query.filter(Notification.id.in_(unread_ids)).update({"read_at": now()}, synchronize_session=False)
        db.session.commit()
    return html
