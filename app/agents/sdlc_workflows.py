"""
Base SDLC workflows (problem statement section 15) as plain data: for each of
the seven models, its phases and, per phase, the ten things section 15 asks
for. The workflow agent (workflow_agent.py) may only ADD scenario-specific
items to these phases; it cannot remove or rename a phase or a base item.

Approval gates follow section 16's mandatory human approvals (requirement
baselines, regulatory interpretations, high-risk security requirements, SDLC
selection, changes to approved requirements, production readiness).
"""

# Field names follow the section 15 list, in its order.
WORKFLOW_FIELDS = (
    "activities",               # Activities within each phase
    "responsible_roles",        # Responsible roles
    "deliverables",             # Expected deliverables
    "security_activities",      # Security activities
    "testing_requirements",     # Testing requirements
    "compliance_checkpoints",   # Compliance checkpoints
    "approval_gates",           # Human-approval gates
    "entry_criteria",           # Entry and exit criteria
    "exit_criteria",
    "traceability_requirements",  # Traceability requirements
)

PM, BA, PO, ARCH, DEV, QA, SEC, COMP, OPS = (
    "Project manager", "Business analyst", "Product owner", "Architect", "Developers",
    "Testers", "Security team", "Compliance officer", "Operations")

TRACE_REQ = "Every requirement keeps its id and link to its source statement"
TRACE_TEST = "Every test case references the requirement ids it verifies"
TRACE_CODE = "Commits and changes reference requirement ids"
GATE_BASELINE = "Requirements baseline approved by the product owner and compliance officer"
GATE_PROD = "Production readiness approved by the project manager, security team and compliance officer"


def phase(name, activities, roles, deliverables, security, testing, compliance, approvals, entry, exit_, trace):
    return {"name": name, "activities": activities, "responsible_roles": roles, "deliverables": deliverables,
            "security_activities": security, "testing_requirements": testing,
            "compliance_checkpoints": compliance, "approval_gates": approvals,
            "entry_criteria": entry, "exit_criteria": exit_, "traceability_requirements": trace}


BASE_WORKFLOWS = {
    "Waterfall": [
        phase("Requirements", ["Elicit and document all requirements up front"], [BA, PO, COMP],
              ["Software Requirements Specification"], ["Identify security and privacy requirements"],
              ["Define acceptance criteria for every requirement"], ["Map requirements to applicable regulations"],
              [GATE_BASELINE], ["Project charter approved"], ["Requirements baseline signed off"], [TRACE_REQ]),
        phase("Design", ["Produce architecture and detailed design"], [ARCH, DEV, SEC],
              ["Design specification"], ["Security architecture review"], ["Plan system and integration tests"],
              ["Check design against compliance requirements"], ["Design approved by the architect"],
              ["Requirements baseline signed off"], ["Design review passed"], ["Design elements reference requirement ids"]),
        phase("Implementation", ["Build all components to the approved design"], [DEV],
              ["Source code", "Build artefacts"], ["Secure coding standard; code review"], ["Unit tests for every component"],
              [], [], ["Design review passed"], ["All components built and unit tested"], [TRACE_CODE]),
        phase("Verification", ["System, integration and acceptance testing"], [QA, PO],
              ["Test report"], ["Security testing before release"], ["System test against the SRS", "User acceptance test"],
              ["Compliance evidence collected"], ["Acceptance approved by the product owner"],
              ["All components built and unit tested"], ["Acceptance criteria met"], [TRACE_TEST]),
        phase("Deployment", ["Release to production"], [OPS, PM], ["Release notes", "Operations runbook"],
              ["Production hardening checklist"], ["Smoke test in production"], ["Compliance sign-off before go-live"],
              [GATE_PROD], ["Acceptance criteria met"], ["System live and monitored"], ["Release notes list the requirement ids delivered"]),
        phase("Maintenance", ["Fix defects; handle change requests through change control"], [OPS, DEV, PM],
              ["Change log"], ["Patch vulnerabilities"], ["Regression tests for every change"],
              ["Re-check compliance for each change"], ["Changes to approved requirements approved by the product owner"],
              ["System live"], ["System retired or replaced"], ["Change requests reference requirement ids"]),
    ],
    "V-Model": [
        phase("Requirements analysis", ["Specify requirements", "Write acceptance test plan"], [BA, PO, QA, COMP],
              ["Software Requirements Specification", "Acceptance test plan"], ["Identify security and privacy requirements"],
              ["Acceptance tests defined for every requirement"], ["Map requirements to applicable regulations"],
              [GATE_BASELINE], ["Project charter approved"], ["Requirements and acceptance test plan approved"], [TRACE_REQ]),
        phase("System and architecture design", ["Design system and architecture", "Write system and integration test plans"],
              [ARCH, QA, SEC], ["Architecture specification", "System and integration test plans"],
              ["Threat assessment of the architecture"], ["System and integration tests defined"],
              ["Design checked against compliance requirements"], ["Architecture approved by the architect"],
              ["Requirements approved"], ["Design and test plans reviewed"], ["Each design element traces to requirement ids"]),
        phase("Detailed design", ["Design modules", "Write unit test plans"], [DEV, QA], ["Module designs", "Unit test plans"],
              ["Secure design review of modules"], ["Unit tests defined for every module"], [], [],
              ["Architecture approved"], ["Module designs reviewed"], ["Modules trace to design elements"]),
        phase("Implementation", ["Code modules to the detailed design"], [DEV], ["Source code"],
              ["Secure coding standard; code review"], ["Static analysis on every module"], [], [],
              ["Module designs reviewed"], ["Code complete and reviewed"], [TRACE_CODE]),
        phase("Unit and integration testing", ["Execute unit and integration test plans"], [DEV, QA],
              ["Unit and integration test reports"], ["Security tests of interfaces"], ["All unit and integration tests pass"],
              [], [], ["Code complete"], ["Integration tests passed"], [TRACE_TEST]),
        phase("System and acceptance testing", ["Execute system and acceptance test plans"], [QA, PO, COMP],
              ["System test report", "Acceptance report"], ["Penetration test before release"],
              ["All system and acceptance tests pass"], ["Compliance evidence reviewed by the compliance officer"],
              ["Acceptance approved by the product owner"], ["Integration tests passed"], ["Acceptance criteria met"],
              ["Test results traced back to every requirement"]),
        phase("Deployment", ["Release to production"], [OPS, PM], ["Release notes"], ["Production hardening checklist"],
              ["Smoke test in production"], ["Compliance sign-off before go-live"], [GATE_PROD],
              ["Acceptance criteria met"], ["System live and monitored"], ["Release notes list the requirement ids delivered"]),
    ],
    "Incremental": [
        phase("Overall requirements and architecture", ["Capture core requirements", "Define architecture and increment plan"],
              [BA, PO, ARCH], ["Requirements overview", "Architecture", "Increment plan"], ["Identify security requirements"],
              ["Define acceptance criteria"], ["Map requirements to applicable regulations"], [GATE_BASELINE],
              ["Project charter approved"], ["Increment plan approved"], [TRACE_REQ]),
        phase("Increment development", ["Design and build the next increment"], [DEV, ARCH],
              ["Working increment"], ["Secure coding; code review"], ["Unit and integration tests for the increment"],
              [], [], ["Increment scope agreed"], ["Increment built and tested"], [TRACE_CODE]),
        phase("Increment testing and release", ["Test the increment with the existing system", "Release the increment"],
              [QA, OPS, PO], ["Test report", "Release notes"], ["Security regression tests"],
              ["Regression tests on earlier increments"], ["Compliance check of the increment"],
              ["Increment release approved by the product owner"], ["Increment built and tested"],
              ["Increment live"], [TRACE_TEST]),
        phase("Final integration and acceptance", ["Integrate all increments; final acceptance"], [QA, PO, COMP, PM],
              ["Acceptance report"], ["Final security assessment"], ["End-to-end acceptance tests"],
              ["Compliance sign-off"], [GATE_PROD], ["All increments live"], ["Acceptance criteria met"],
              ["Every requirement traced to the increment that delivered it"]),
    ],
    "Agile": [
        phase("Inception", ["Form the product backlog", "Agree definition of done"], [PO, BA, ARCH],
              ["Product backlog", "Definition of done"], ["Security requirements as backlog items"],
              ["Acceptance criteria on every story"], ["Regulatory requirements flagged in the backlog"],
              ["Initial backlog approved by the product owner"], ["Vision agreed"], ["Backlog ready for the first sprint"], [TRACE_REQ]),
        phase("Sprint planning", ["Select and refine stories for the sprint"], [PO, DEV, QA], ["Sprint backlog"],
              ["Security review of selected stories"], ["Test approach per story"], [], [],
              ["Backlog ready"], ["Sprint goal agreed"], ["Stories keep their requirement ids"]),
        phase("Sprint", ["Build and test stories"], [DEV, QA], ["Potentially shippable increment"],
              ["Secure coding; peer review"], ["Automated unit and acceptance tests"], [], [],
              ["Sprint goal agreed"], ["Definition of done met"], [TRACE_CODE]),
        phase("Sprint review and retrospective", ["Demonstrate the increment; gather feedback; improve the process"],
              [PO, DEV, QA], ["Updated backlog"], [], ["Acceptance by the product owner"], [],
              ["Increment accepted by the product owner"], ["Sprint finished"], ["Feedback added to the backlog"],
              ["Feedback items linked to the stories they change"]),
        phase("Release", ["Release accepted increments to production"], [OPS, PO, PM], ["Release notes"],
              ["Release security checklist"], ["Regression and smoke tests"], ["Compliance check before release"],
              [GATE_PROD], ["Increments accepted"], ["Release live and monitored"], ["Release notes list the requirement ids delivered"]),
    ],
    "Spiral": [
        phase("Objectives and constraints", ["Define objectives, alternatives and constraints for this cycle"], [PM, BA, ARCH],
              ["Cycle objectives"], ["Identify security objectives"], [], ["Identify applicable regulations"],
              ["Cycle plan approved by the project manager"], ["Previous cycle reviewed"], ["Objectives agreed"], [TRACE_REQ]),
        phase("Risk analysis and prototyping", ["Analyse risks", "Build prototypes to resolve the largest risks"],
              [ARCH, DEV, SEC], ["Risk analysis", "Prototype"], ["Threat analysis of the chosen approach"],
              ["Evaluate prototypes against risk criteria"], [], ["Risk resolution approved by the architect"],
              ["Objectives agreed"], ["Top risks resolved or accepted"], ["Risks linked to the requirement ids they affect"]),
        phase("Engineering", ["Design, build and verify this cycle's product"], [DEV, QA], ["Cycle deliverable"],
              ["Secure coding; security testing"], ["Verification of the cycle deliverable"], [], [],
              ["Top risks resolved or accepted"], ["Deliverable verified"], [TRACE_CODE]),
        phase("Evaluation and planning", ["Customer evaluation; plan the next cycle"], [PO, PM], ["Next-cycle plan"],
              [], ["Customer evaluation"], ["Compliance review of the deliverable"], ["Continue/stop decision by the project manager"],
              ["Deliverable verified"], ["Next cycle planned or project concluded"], [TRACE_TEST]),
        phase("Release", ["Release the final product"], [OPS, PM], ["Release notes"], ["Production hardening checklist"],
              ["Acceptance and smoke tests"], ["Compliance sign-off before go-live"], [GATE_PROD],
              ["Final cycle evaluated"], ["System live and monitored"], ["Release notes list the requirement ids delivered"]),
    ],
    "DevSecOps": [
        phase("Plan", ["Refine backlog with security and compliance stories"], [PO, BA, SEC, COMP],
              ["Backlog with security stories"], ["Threat modelling of new features"],
              ["Acceptance criteria and security acceptance tests on every story"], ["Compliance requirements as backlog items"],
              [GATE_BASELINE], ["Vision agreed"], ["Stories ready"], [TRACE_REQ]),
        phase("Code", ["Develop with peer review"], [DEV], ["Source code"], ["Secure coding; secrets scanning"],
              ["Unit tests"], [], [], ["Stories ready"], ["Code reviewed and merged"], [TRACE_CODE]),
        phase("Build", ["Automated build in the pipeline"], [DEV, OPS], ["Build artefacts"],
              ["Static application security testing (SAST)", "Dependency vulnerability scan"],
              ["Automated unit and integration tests"], [], [], ["Code merged"], ["Pipeline green"], ["Builds record the commits and requirement ids they include"]),
        phase("Test", ["Automated test stages"], [QA, SEC], ["Test and scan reports"],
              ["Dynamic application security testing (DAST)"], ["Automated acceptance and regression tests"],
              ["Compliance checks as automated pipeline tests"], [], ["Pipeline green"], ["All test and security gates pass"], [TRACE_TEST]),
        phase("Release and deploy", ["Automated deployment to production"], [OPS], ["Deployed release"],
              ["Release security gate; infrastructure configuration scan"], ["Smoke tests after deployment"],
              ["Compliance evidence captured automatically"],
              ["Production readiness of the pipeline and of each major release approved by the project manager, "
               "security team and compliance officer", "High-risk changes approved by the security team before deploy"],
              ["All gates pass"], ["Release live"], ["Release records list the requirement ids delivered"]),
        phase("Operate and monitor", ["Monitor, respond to incidents, feed findings back to Plan"], [OPS, SEC],
              ["Monitoring dashboards", "Incident reports"], ["Security monitoring and incident response"],
              ["Production checks"], ["Audit logs retained"], [], ["Release live"], ["Findings fed back to the backlog"],
              ["Incidents linked to the requirement ids involved"]),
    ],
    "Agile-V": [
        phase("Requirements baseline", ["Specify requirements and acceptance tests up front", "Build the initial backlog from the baseline"],
              [BA, PO, QA, COMP], ["Requirements specification", "Acceptance test plan", "Product backlog"],
              ["Identify security and privacy requirements"], ["Acceptance tests defined for every requirement"],
              ["Map requirements to applicable regulations"], [GATE_BASELINE], ["Project charter approved"],
              ["Baseline and backlog approved"], [TRACE_REQ]),
        phase("Architecture and verification planning", ["Design the architecture", "Plan verification for each level"],
              [ARCH, QA, SEC], ["Architecture", "Verification plan"], ["Threat assessment of the architecture"],
              ["System and integration tests planned"], ["Design checked against compliance requirements"],
              ["Architecture approved by the architect"], ["Baseline approved"], ["Verification plan reviewed"],
              ["Design elements trace to requirement ids"]),
        phase("Iterative development", ["Build in sprints; verify each increment against its V-level tests"], [DEV, QA],
              ["Verified increments"], ["Secure coding; peer review"], ["Unit, integration and regression tests every sprint"],
              [], ["Changes to approved requirements approved by the product owner and compliance officer"],
              ["Verification plan reviewed"], ["Increment verified"], [TRACE_CODE]),
        phase("System verification", ["Integrate increments; run system tests"], [QA, SEC], ["System test report"],
              ["Penetration test"], ["All system tests pass"], ["Compliance evidence collected"], [],
              ["Increments verified"], ["System tests passed"], [TRACE_TEST]),
        phase("Validation and release", ["Acceptance testing; release"], [PO, COMP, OPS, PM], ["Acceptance report", "Release notes"],
              ["Production hardening checklist"], ["Acceptance tests against the baseline"],
              ["Compliance sign-off before go-live"], [GATE_PROD], ["System tests passed"], ["System live and monitored"],
              ["Every requirement traced to its acceptance result"]),
    ],
}
