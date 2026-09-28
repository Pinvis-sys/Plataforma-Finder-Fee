"""Área interna (Telas 7 a 10): gestão, comercial, financeiro e administração."""
from __future__ import annotations

import copy
import os
from datetime import date
from decimal import Decimal

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for)

from .. import cnpj as cn
from .. import engine as eng
from .. import reports
from .. import security as sec
from .. import services as svc
from ..core import db, now, today
from ..models import (STAFF_ROLES, TERM_KEYS, AuditLog, CommissionLine, Contract, DirectInvite, Installment, KnownCompany,
                      Material, Partner, PartnerInvoice, Receipt, Referral, ReportEdition, ReviewRequest, SurveyResponse,
                      TermDocument, User, set_setting, setting)
from ..rules import (DEFAULT_RULES, PENDING_DECISIONS, RuleSet, active_ruleset, new_version, tax_rate_on)
from .helpers import fail, ok, parse_date, parse_money

bp = Blueprint("manage", __name__)

COMMERCIAL = ("manager", "commercial")
FINANCE = ("finance", "manager")
ANY_STAFF = STAFF_ROLES


def actor() -> str:
    return sec.current_user().email


# ================================================================== Tela 7: painel
@bp.route("/")
@sec.roles_required(*ANY_STAFF)
def painel():
    svc.expire_protections()
    q = svc.manager_queues()
    rules = active_ruleset()
    return render_template("g_painel.html", q=q, rules=rules, pending=PENDING_DECISIONS)


# ================================================================== indicações
@bp.route("/indicacoes")
@sec.roles_required(*ANY_STAFF)
def indicacoes():
    flt = request.args.get("f", "abertas")
    qs = Referral.query
    if flt == "abertas":
        qs = qs.filter(Referral.eligibility.in_(("recebida", "em_validacao")))
    elif flt == "atendimento":
        qs = qs.filter(Referral.eligibility == "aceita", Referral.waiting.is_(False))
    elif flt == "espera":
        qs = qs.filter(Referral.eligibility == "aceita", Referral.waiting.is_(True))
    elif flt in ("rejeitada", "expirada"):
        qs = qs.filter(Referral.eligibility == flt)
    refs = qs.order_by(Referral.created_at.desc()).all()
    return render_template("g_indicacoes.html", refs=refs, flt=flt)


@bp.route("/indicacoes/<int:rid>", methods=["GET", "POST"])
@sec.roles_required(*ANY_STAFF)
def indicacao(rid):
    ref = db.session.get(Referral, rid) or abort(404)
    if request.method == "POST":
        u = sec.current_user()
        if not u.has_role(*COMMERCIAL):
            abort(403)
        f = request.form
        act = f.get("action")
        try:
            if act in ("start_review", "accept", "reject"):
                fit = {"sim": True, "nao": False}.get(f.get("fits_target"))
                svc.validate_referral(ref, act, actor(), f.get("reason"), fit)
                ok({"start_review": "Indicação em validação.", "accept": "Indicação aceita.", "reject": "Indicação não aceita. O parceiro foi avisado."}[act])
            elif act == "consent":
                svc.register_consent(ref, f.get("evidence", "").strip(), actor())
                ok("Consentimento registrado.")
            elif act == "stage":
                svc.set_stage(ref, f.get("stage"), actor(), f.get("lost_reason"), f.get("next_action"))
                ok("Etapa atualizada.")
            elif act == "start_service":
                svc.start_service(ref, actor(), f.get("override_reason"))
                ok("Atendimento iniciado.")
            elif act == "extend":
                svc.extend_protection(ref, int(f.get("days") or 0), f.get("reason", "").strip(), actor())
                ok("Proteção prorrogada.")
            elif act == "flags":
                ref.delayed_capacity = bool(f.get("delayed_capacity"))
                ref.internal_notes = f.get("internal_notes")
                fit = {"sim": True, "nao": False}.get(f.get("fits_target"))
                ref.fits_target = fit
                svc.audit(actor(), "referral.flags", "referral", ref.id)
                db.session.commit()
                ok("Anotações salvas.")
            elif act == "contract":
                items = []
                for i in range(1, 4):
                    if f.get(f"i{i}_desc") or f.get(f"i{i}_amount"):
                        items.append(eng.ScheduleItem(f.get(f"i{i}_desc") or f"Item {i}", f.get(f"i{i}_kind", "retainer"),
                                                      parse_money(f.get(f"i{i}_amount")), parse_date(f.get(f"i{i}_due"), "data de vencimento"),
                                                      int(f.get(f"i{i}_months") or 1)))
                c = svc.win_contract(ref, f.get("product", "").strip(), f.get("audience") or None,
                                     parse_date(f.get("signed_at"), "data de assinatura"), items, actor())
                ok("Contrato registrado. As parcelas foram geradas.")
                return redirect(url_for("manage.contrato", cid=c.id))
        except (svc.ServiceError, ValueError) as e:
            fail(e)
        return redirect(url_for("manage.indicacao", rid=rid))
    return render_template("g_indicacao.html", ref=ref, can_edit=sec.current_user().has_role(*COMMERCIAL),
                           duplicates=[], rules=active_ruleset().rules)


@bp.route("/revisoes/<int:rrid>", methods=["POST"])
@sec.roles_required(*COMMERCIAL)
def revisao(rrid):
    rr = db.session.get(ReviewRequest, rrid) or abort(404)
    try:
        svc.resolve_review(rr, request.form.get("resolution", "").strip(), actor())
        ok("Resposta enviada ao parceiro.")
    except svc.ServiceError as e:
        fail(e)
    return redirect(request.referrer or url_for("manage.painel"))


# ================================================================== Tela 9: parceiros
@bp.route("/parceiros")
@sec.roles_required(*ANY_STAFF)
def parceiros():
    partners = Partner.query.order_by(Partner.application_at.desc()).all()
    return render_template("g_parceiros.html", partners=partners, q=svc.manager_queues()["candidaturas"],
                           used=svc.recruit_slots_used(), limit=active_ruleset().rules["pilot_recruit_limit"])


@bp.route("/convites", methods=["GET", "POST"])
@sec.roles_required(*COMMERCIAL)
def convites():
    if request.method == "POST":
        inv = svc.create_direct_invite(actor(), request.form.get("note", ""))
        ok("Convite criado. Copie o link e envie ao candidato.")
        return redirect(url_for("manage.convites"))
    invites = DirectInvite.query.order_by(DirectInvite.created_at.desc()).all()
    base = current_app.config["PUBLIC_BASE_URL"]
    return render_template("g_convites.html", invites=invites, base=base)


@bp.route("/parceiros/<int:pid>", methods=["GET", "POST"])
@sec.roles_required(*ANY_STAFF)
def parceiro(pid):
    p = db.session.get(Partner, pid) or abort(404)
    if request.method == "POST":
        u = sec.current_user()
        f = request.form
        act = f.get("action")
        try:
            if act in ("verify_cnpj", "approve", "reject", "workshop", "conflict", "level", "suspend", "reactivate", "link", "bank"):
                if not u.has_role(*COMMERCIAL):
                    abort(403)
            if act == "bank" and not u.has_role(*FINANCE):
                abort(403)
            if act == "verify_cnpj":
                p.cnpj_verified = bool(f.get("value"))
                svc.audit(actor(), "partner.verify_cnpj", "partner", p.id, value=p.cnpj_verified)
                db.session.commit()
                ok("Situação do CNPJ atualizada.")
            elif act == "approve":
                svc.review_partner(p, True, actor(), level=int(f.get("level") or 1), kind=f.get("kind"))
                ok("Parceiro aprovado.")
            elif act == "reject":
                svc.review_partner(p, False, actor(), reason=f.get("reason", "").strip())
                ok("Candidatura não aprovada.")
            elif act == "workshop":
                svc.mark_workshop(p, actor())
                ok("Workshop registrado.")
            elif act == "conflict":
                p.conflict_notes = f.get("conflict_notes", "").strip() or None
                svc.audit(actor(), "partner.conflict", "partner", p.id)
                db.session.commit()
                ok("Anotação de conflito de interesse salva.")
            elif act == "level":
                p.level = int(f.get("level") or 1)
                svc.audit(actor(), "partner.level", "partner", p.id, level=p.level)
                db.session.commit()
                ok("Nível atualizado.")
            elif act in ("suspend", "reactivate"):
                p.status = "suspenso" if act == "suspend" else "ativo"
                svc.audit(actor(), f"partner.{act}", "partner", p.id, reason=f.get("reason"))
                db.session.commit()
                ok("Situação atualizada.")
            elif act == "link":
                inv = Partner.query.filter_by(code=(f.get("inviter") or "").strip().upper()).first() if f.get("inviter") else None
                svc.link_inviter(p, inv, f.get("reason", "").strip(), actor())
                ok("Vínculo de convite corrigido.")
            elif act == "bank":
                p.bank_verified = bool(f.get("value")) and bool(p.bank_holder_doc) and cn.digits(p.bank_holder_doc) == p.cnpj
                if f.get("value") and not p.bank_verified:
                    flash("A titularidade não bate com o CNPJ do parceiro. Não foi possível marcar como conferida.", "error")
                else:
                    ok("Conferência bancária atualizada.")
                svc.audit(actor(), "partner.bank_verify", "partner", p.id, value=p.bank_verified)
                db.session.commit()
        except (svc.ServiceError, ValueError) as e:
            fail(e)
        return redirect(url_for("manage.parceiro", pid=pid))
    refs = Referral.query.filter_by(partner_id=p.id).order_by(Referral.created_at.desc()).all()
    recruited = Partner.query.filter_by(invited_by_id=p.id).all()
    return render_template("g_parceiro.html", p=p, refs=refs, recruited=recruited, can_edit=sec.current_user().has_role(*COMMERCIAL),
                           can_fin=sec.current_user().has_role(*FINANCE))


# ================================================================== Tela 8: financeiro
@bp.route("/financeiro")
@sec.roles_required(*FINANCE)
def financeiro():
    q = svc.manager_queues()
    programadas = {}
    for l in CommissionLine.query.filter_by(state="programada").order_by(CommissionLine.cycle).all():
        programadas.setdefault((l.beneficiary_id, l.cycle), []).append(l)
    contracts = Contract.query.filter_by(is_simulation=False).order_by(Contract.created_at.desc()).all()
    partners = {p.id: p for p in Partner.query.all()}
    return render_template("g_financeiro.html", q=q, programadas=programadas, contracts=contracts, partners=partners,
                           active_partners=[p for p in partners.values() if p.status == "ativo"],
                           recent=CommissionLine.query.order_by(CommissionLine.id.desc()).limit(40).all())


@bp.route("/financeiro/linhas/<int:lid>", methods=["POST"])
@sec.roles_required(*FINANCE)
def linha(lid):
    l = db.session.get(CommissionLine, lid) or abort(404)
    f = request.form
    try:
        act = f.get("action")
        if act == "approve":
            svc.approve_line(l, actor())
            ok("Comissão aprovada. O parceiro foi avisado para enviar a nota fiscal.")
        elif act == "check":
            svc.mark_checked(l, True, None, actor())
            ok("Parcela conferida.")
        elif act == "diverge":
            svc.mark_checked(l, False, f.get("note", "").strip(), actor())
            ok("Divergência registrada.")
    except svc.ServiceError as e:
        fail(e)
    return redirect(request.referrer or url_for("manage.financeiro"))


@bp.route("/financeiro/notas/<int:iid>", methods=["POST"])
@sec.roles_required(*FINANCE)
def nota(iid):
    inv = db.session.get(PartnerInvoice, iid) or abort(404)
    try:
        svc.review_invoice(inv, request.form.get("action") == "ok", request.form.get("reason", "").strip(), actor())
        ok("Nota fiscal conferida." if request.form.get("action") == "ok" else "Nota recusada. O parceiro foi avisado.")
    except svc.ServiceError as e:
        fail(e)
    return redirect(url_for("manage.financeiro"))


@bp.route("/financeiro/pagar", methods=["POST"])
@sec.roles_required(*FINANCE)
def pagar():
    f = request.form
    try:
        p = db.session.get(Partner, int(f["partner_id"])) or abort(404)
        out = svc.pay_cycle(p, date.fromisoformat(f["cycle"]), f.get("reference", "").strip(), actor())
        ok("Saldo líquido negativo: acumulado para o mês seguinte." if out["carried"] else f"Pagamento de {out['paid']:.2f} registrado.")
    except (svc.ServiceError, ValueError, KeyError) as e:
        fail(e)
    return redirect(url_for("manage.financeiro"))


@bp.route("/financeiro/ajuste", methods=["POST"])
@sec.roles_required(*FINANCE)
def ajuste():
    f = request.form
    try:
        p = db.session.get(Partner, int(f["partner_id"])) or abort(404)
        svc.add_adjustment(p, parse_money(f.get("amount")), f.get("reason", "").strip(), actor())
        ok("Ajuste lançado.")
    except (svc.ServiceError, ValueError, KeyError) as e:
        fail(e)
    return redirect(url_for("manage.financeiro"))


@bp.route("/contratos/<int:cid>", methods=["GET", "POST"])
@sec.roles_required(*ANY_STAFF)
def contrato(cid):
    c = db.session.get(Contract, cid) or abort(404)
    rules = c.referral.rule_set.rules if c.referral.rule_set else active_ruleset().rules
    if request.method == "POST":
        if not sec.current_user().has_role(*FINANCE):
            abort(403)
        f = request.form
        try:
            act = f.get("action")
            if act == "cancel":
                svc.cancel_contract(c, parse_date(f.get("when"), "data"), f.get("reason", "").strip(), actor())
                ok("Contrato cancelado. Parcelas sem recebimento foram canceladas.")
            elif act == "receipt":
                inst = db.session.get(Installment, int(f["installment_id"])) or abort(404)
                _, lines, warns = svc.register_receipt(inst, parse_date(f.get("received_at"), "data de recebimento"), parse_money(f.get("amount")),
                                                       f.get("nf_number", "").strip(), parse_money(f.get("nf_value")), parse_money(f.get("taxes")), actor())
                for w in warns:
                    flash(w, "warn")
                ok(f"Recebimento registrado. {len(lines)} linha(s) de comissão gerada(s), aguardando conferência." if lines
                   else "Recebimento registrado. Parcela fora da janela: não gera comissão.")
            elif act == "discount":
                inst = db.session.get(Installment, int(f["installment_id"])) or abort(404)
                svc.apply_discount(inst, parse_money(f.get("new_gross")), f.get("reason", "").strip(), actor())
                ok("Valor da parcela ajustado.")
        except (svc.ServiceError, ValueError, KeyError) as e:
            fail(e)
        return redirect(url_for("manage.contrato", cid=cid))
    rate = {i.id: tax_rate_on(rules, i.due_date) for i in c.installments}
    return render_template("g_contrato.html", c=c, rate=rate, can_fin=sec.current_user().has_role(*FINANCE))


@bp.route("/pagamentos-pendentes")
@sec.roles_required(*FINANCE)
def recebimento():
    return redirect(url_for("manage.financeiro"))


# ================================================================== relatório semanal
@bp.route("/relatorios", methods=["GET", "POST"])
@sec.roles_required(*ANY_STAFF)
def relatorios():
    if request.method == "POST":
        if not sec.current_user().has_role("manager"):
            abort(403)
        f = request.form
        try:
            if f.get("action") == "generate":
                end = date.fromisoformat(f["week_end"]) if f.get("week_end") else reports.last_sunday(today())
                if end.weekday() != 6:
                    raise svc.ServiceError("A semana fecha no domingo. Escolha um domingo.")
                ed = reports.generate_weekly(end, current_app.config["REPORT_DIR"], f.get("notes", "").strip(), force=bool(f.get("force")))
                ok(f"Edição {ed.edition} emitida.")
            elif f.get("action") == "legal":
                set_setting("legal_opinion_date", f.get("legal_date") or "")
                svc.audit(actor(), "setting.legal_opinion", None, None, value=f.get("legal_date"))
                db.session.commit()
                ok("Data do parecer jurídico registrada.")
        except (svc.ServiceError, ValueError) as e:
            fail(e)
        return redirect(url_for("manage.relatorios"))
    eds = ReportEdition.query.order_by(ReportEdition.period_end.desc()).all()
    crit = reports.evaluate_criteria(today())
    return render_template("g_relatorios.html", eds=eds, crit=crit, labels=reports.STATUS_LABEL,
                           legal=setting("legal_opinion_date"), last=reports.last_sunday(today()))


@bp.route("/relatorios/<int:eid>/<fmt>")
@sec.roles_required(*ANY_STAFF)
def baixar_relatorio(eid, fmt):
    ed = db.session.get(ReportEdition, eid) or abort(404)
    path = ed.xlsx_path if fmt == "xlsx" else ed.pdf_path if fmt == "pdf" else None
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True)


# ================================================================== Tela 10: parâmetros
FORM_PARAMS = [
    ("commission_pct", "Comissão padrão", "pct"), ("bonus_pct", "Bônus de expansão (Camada 2)", "pct"),
    ("window_months", "Janela de remuneração (meses)", "int"), ("annual_cap_per_partner", "Teto anual de indicações por parceiro (interno)", "int"),
    ("aggregate_cap", "Teto agregado do programa (interno, vazio = sem teto)", "int?"), ("protection_days", "Proteção comercial (dias)", "int"),
    ("nf_deadline_day", "Prazo mensal da NF (dia do mês)", "int?"), ("sla_review_days", "Prazo de análise (dias úteis)", "int"),
    ("first_contact_days", "Primeiro contato (dias úteis)", "int"), ("high_frequency_threshold", "Alerta de recorrência (aceitas em 12 meses)", "int"),
    ("pilot_recruit_limit", "Recrutamentos no piloto (total)", "int"), ("channel_cost_limit", "Limite de custo do canal (% da receita líquida, vazio = a definir)", "pct?"),
]


@bp.route("/parametros", methods=["GET", "POST"])
@sec.roles_required("admin", "manager")
def parametros():
    cur = active_ruleset()
    if request.method == "POST":
        if not sec.current_user().has_role("admin"):
            abort(403)
        f = request.form
        try:
            if not f.get("note", "").strip():
                raise svc.ServiceError("Informe o motivo da mudança. Ele fica registrado na versão.")
            changes = {}
            for key, _, kind in FORM_PARAMS:
                raw = (f.get(key) or "").strip().replace(",", ".")
                if raw == "":
                    if kind.endswith("?"):
                        changes[key] = None
                        continue
                    raise svc.ServiceError("Preencha todos os campos obrigatórios.")
                if kind.startswith("pct"):
                    changes[key] = str(Decimal(raw) / 100)
                else:
                    changes[key] = int(raw)
            changes["aggregate_cap_period"] = f.get("aggregate_cap_period", "quarter")
            changes["duplicate_scope"] = f.get("duplicate_scope", "cnpj_base")
            changes["window_end_inclusive"] = bool(f.get("window_end_inclusive"))
            changes["consent_required"] = bool(f.get("consent_required"))
            tax = (f.get("tax_total") or "").replace(",", ".")
            if tax:
                changes["tax_rates"] = [{"from": "2026-01-01", "total": str(Decimal(tax) / 100), "note": "ilustrativo"}]
            for k in ("commission_pct", "bonus_pct"):
                if not (Decimal("0") <= Decimal(changes[k]) <= Decimal("1")):
                    raise svc.ServiceError("Percentuais devem ficar entre 0% e 100%.")
            rs = new_version(cur, changes, actor(), f["note"].strip())
            svc.audit(actor(), "rules.new_version", "rule_set", rs.id, note=f["note"])
            db.session.commit()
            ok(f"Nova versão criada: {rs.name}. Indicações já aceitas continuam com a regra vigente na data do aceite.")
        except (svc.ServiceError, ValueError, ArithmeticError) as e:
            fail(e if isinstance(e, svc.ServiceError) else ValueError("Confira os números informados."))
        return redirect(url_for("manage.parametros"))
    versions = RuleSet.query.order_by(RuleSet.id.desc()).all()
    rules = cur.rules
    pend = {k: v for k, v in PENDING_DECISIONS.items()}
    return render_template("g_parametros.html", cur=cur, rules=rules, versions=versions, fields=FORM_PARAMS, pending=pend,
                           tax=Decimal(rules["tax_rates"][-1]["total"]) * 100, is_admin=sec.current_user().has_role("admin"))


# ================================================================== termos, materiais, base comercial, auditoria
@bp.route("/termos", methods=["GET", "POST"])
@sec.roles_required("admin", "manager")
def termos():
    if request.method == "POST":
        f = request.form
        try:
            if f.get("action") == "term":
                d = db.session.get(TermDocument, f["key"]) or abort(404)
                if not sec.current_user().has_role("admin"):
                    abort(403)
                body = f.get("body", "").strip()
                if not body:
                    raise svc.ServiceError("O texto do termo não pode ficar vazio.")
                if body != d.body:
                    d.body, d.version = body, d.version + 1
                d.legal_approved = bool(f.get("legal_approved"))
                d.updated_at = now()
                svc.audit(actor(), "term.update", "term_document", None, key=d.key, version=d.version, legal=d.legal_approved)
                db.session.commit()
                ok(f"Termo salvo (versão {d.version}).")
            elif f.get("action") == "material":
                db.session.add(Material(title=f.get("title", "").strip(), kind=f.get("kind", "link"), url=f.get("url", "").strip(),
                                        position=int(f.get("position") or 0)))
                db.session.commit()
                ok("Material publicado.")
            elif f.get("action") == "material_del":
                m = db.session.get(Material, int(f["id"]))
                if m:
                    db.session.delete(m)
                    db.session.commit()
                ok("Material removido.")
        except (svc.ServiceError, ValueError) as e:
            fail(e)
        return redirect(url_for("manage.termos"))
    return render_template("g_termos.html", docs=TermDocument.query.order_by(TermDocument.key).all(),
                           mats=Material.query.order_by(Material.position).all())


@bp.route("/materiais")
@sec.roles_required("admin", "manager")
def materiais():
    return redirect(url_for("manage.termos"))


@bp.route("/base", methods=["GET", "POST"])
@sec.roles_required(*COMMERCIAL)
def base():
    if request.method == "POST":
        f = request.form
        try:
            raw = f.get("cnpj", "")
            if f.get("action") == "del":
                kc = db.session.get(KnownCompany, int(f["id"]))
                if kc:
                    db.session.delete(kc)
                    svc.audit(actor(), "known_company.delete", "known_company", kc.id)
                    db.session.commit()
                ok("Registro removido.")
            else:
                if not cn.is_valid(raw):
                    raise svc.ServiceError("CNPJ inválido.")
                num = cn.normalize(raw)
                db.session.add(KnownCompany(cnpj=num, cnpj_base=cn.base(num), kind=f.get("kind", "cliente_atual"), note=f.get("note")))
                svc.audit(actor(), "known_company.add", "known_company", None, cnpj=num)
                db.session.commit()
                ok("Empresa adicionada à base comercial.")
        except (svc.ServiceError, ValueError) as e:
            fail(e)
        return redirect(url_for("manage.base"))
    return render_template("g_base.html", items=KnownCompany.query.order_by(KnownCompany.created_at.desc()).all())


@bp.route("/auditoria")
@sec.roles_required("admin", "manager")
def auditoria():
    logs = AuditLog.query.order_by(AuditLog.id.desc()).limit(300).all()
    return render_template("g_auditoria.html", logs=logs)


@bp.route("/usuarios", methods=["GET", "POST"])
@sec.roles_required("admin")
def usuarios():
    if request.method == "POST":
        f = request.form
        try:
            if f.get("action") == "create":
                roles = ",".join(r for r in f.getlist("roles") if r in STAFF_ROLES)
                if not roles:
                    raise svc.ServiceError("Escolha ao menos um perfil.")
                from ..seed import create_staff
                u = create_staff(f.get("email", "").strip(), f.get("name", "").strip(), roles, f.get("password", ""))
                u.must_change_password = True
                db.session.commit()
                ok("Usuário criado. Ele precisará trocar a senha no primeiro acesso.")
            elif f.get("action") == "reset":
                u = db.session.get(User, int(f["id"])) or abort(404)
                if len(f.get("password", "")) < 10:
                    raise svc.ServiceError("A senha temporária precisa ter ao menos 10 caracteres.")
                u.set_password(f["password"])
                u.must_change_password, u.failed_logins, u.locked_until = True, 0, None
                svc.audit(actor(), "user.reset_password", "user", u.id)
                db.session.commit()
                ok("Senha temporária definida.")
            elif f.get("action") == "toggle":
                u = db.session.get(User, int(f["id"])) or abort(404)
                if u.id == sec.current_user().id:
                    raise svc.ServiceError("Você não pode desativar o próprio acesso.")
                u.active = not u.active
                svc.audit(actor(), "user.toggle", "user", u.id, active=u.active)
                db.session.commit()
                ok("Acesso atualizado.")
        except (svc.ServiceError, ValueError) as e:
            fail(e)
        return redirect(url_for("manage.usuarios"))
    return render_template("g_usuarios.html", users=User.query.order_by(User.name).all(), roles=STAFF_ROLES)
