"""Simulação do bônus da Camada 2 em banco temporário (ambiente de teste, sem tocar nos dados reais)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from . import cnpj as cn
from . import engine as eng
from . import seed, services as svc
from .core import db


def run() -> str:
    from . import create_app
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True, "SECRET_KEY": "sim"}, web=False)
    out = []
    with app.app_context():
        db.create_all()
        seed.seed_defaults()
        a = seed.make_active_partner("Parceira A (convida)", "70000001", "a@sim.test")
        b = seed.make_active_partner("Parceiro B (recrutado)", "70000002", "b@sim.test", invited_token=a.invite_token)
        r = svc.register_referral(b, {"cnpj": cn.make_valid("70000003"), "decisor_name": "Decisor", "signal": "Simulação"})
        svc.validate_referral(r, "accept", "sim")
        svc.register_consent(r, "simulação", "sim")
        signed = date.today()
        items = [eng.ScheduleItem("Setup", "evento_unico", D("40000"), signed),
                 eng.ScheduleItem("Retainer", "retainer", D("18000"), eng.add_months(signed, 1), months=12)]
        c = svc.win_contract(r, "CGV", None, signed, items, "sim")
        tc = tb = D("0")
        gross = D("0")
        for inst in c.installments:
            if not inst.in_window:
                continue
            _, lines, _ = svc.register_receipt(inst, inst.due_date, inst.gross_amount, f"NF-{inst.id}", inst.gross_amount,
                                               eng.q(inst.gross_amount * D("0.10")), "sim")
            gross += inst.gross_amount
            for l in lines:
                if l.kind == "cliente":
                    tc += l.amount
                else:
                    tb += l.amount
        net = eng.q(gross * D("0.9"))
        out += ["Simulação do bônus da Camada 2 (banco temporário)",
                f"Contrato: setup R$ 40.000 + retainer R$ 18.000 x 12 = {gross} bruto ({len(c.installments)} parcelas)",
                f"Base líquida (impostos 10%): {net}",
                f"Comissão do parceiro B (15%): {tc}",
                f"Bônus de A (50% de cada parcela de B): {tb}",
                f"Total pago pela All Targets: {tc + tb} = {(tc + tb) / net:.1%} da base líquida"]
        db.session.remove()
        db.drop_all()
    return "\n".join(out)
