# CHECKPOINT
## Software Requirements Specification (SRS)

**Project:** CHECKPOINT – Autonomous Deep-Space Crew Health & Mission Intelligence System  
**Document Version:** 1.0  
**Status:** Proposed Technical Specification  
**Mission Environment:** Simulated Mars Transit  
**System Classification:** Offline-First Mission Monitoring and Decision-Support Prototype

---

# 1. Introduction

## 1.1 Purpose

CHECKPOINT is an autonomous crew-health monitoring and mission-intelligence platform designed to simulate the operational requirements of a crewed deep-space Mars transit mission.

The system provides a centralized interface for monitoring crew physiological conditions, tracking environmental hazards, managing medical information, analysing historical telemetry, and coordinating communications between a spacecraft and Earth-based mission control.

The application must remain operational during extended communication interruptions and provide local monitoring, alerting, data storage and reporting capabilities without depending on continuous Earth connectivity.

## 1.2 Project Objectives

The primary objectives are:

1. Provide continuous monitoring of simulated crew-health telemetry.
2. Detect abnormal physiological measurements using configurable rules.
3. Support autonomous onboard decision-support workflows.
4. Simulate realistic Earth-to-Mars communication delays.
5. Maintain persistent local records during offline operation.
6. Provide historical analytics and health-trend visualization.
7. Maintain secure, traceable medical and operational records.
8. Support multiple crew members and mission operators.
9. Provide emergency response and incident-management workflows.
10. Deliver a reliable, maintainable and extensible software architecture.

## 1.3 Scope

CHECKPOINT shall include:

- Crew health monitoring.
- Physiological telemetry simulation.
- Radiation exposure monitoring.
- Environmental monitoring.
- Medical records.
- Medication inventory and administration records.
- Autonomous alert management.
- Earth communication simulation.
- Offline-first data synchronization.
- Mission activity logging.
- Health analytics.
- Emergency response procedures.
- Role-based access control.
- Data export and reporting.

The initial implementation shall use simulated mission data. Hardware integrations and real clinical deployments are outside the initial release scope.

# 2. System Architecture

## 2.1 Architectural Requirements

The system shall follow a modular, offline-first architecture.

The architecture should separate:

- User interface.
- Application services.
- Telemetry processing.
- Medical decision-support rules.
- Local persistence.
- Communication services.
- Authentication and authorization.
- Audit logging.
- Analytics and reporting.

The frontend must not directly manipulate the underlying database or bypass application-level validation.

## 2.2 Recommended Architecture

The proposed architecture consists of the following components:

### A. Presentation Layer

Responsible for displaying:

- Mission dashboard.
- Crew profiles.
- Physiological measurements.
- Health trends.
- Radiation monitoring.
- Medical alerts.
- Communication status.
- Mission logs.
- Emergency procedures.

### B. Application Layer

Responsible for:

- Processing operator requests.
- Validating incoming data.
- Managing application state.
- Executing business rules.
- Coordinating system services.

### C. Telemetry Processing Engine

Responsible for:

- Receiving simulated sensor readings.
- Validating telemetry.
- Detecting missing measurements.
- Calculating derived metrics.
- Identifying abnormal readings.
- Publishing updated crew-health information.

### D. Autonomous Decision-Support Engine

Responsible for evaluating configured health-monitoring rules and generating alerts.

### E. Local Persistence Layer

Responsible for storing:

- Crew information.
- Health measurements.
- Medical records.
- Alerts.
- Mission events.
- Communication messages.
- Audit records.

### F. Earth Communication Layer

Responsible for simulating delayed communication, offline operation, message queuing and synchronization.

# 3. Functional Requirements

## FR-001: Crew Management

The system shall support multiple crew members.

Each crew profile shall contain:

- Unique crew identifier.
- Crew name.
- Mission role.
- Crew status.
- Medical baseline information.
- Relevant medical history.
- Current health status.
- Last telemetry update.

The system shall allow authorized operators to create, view and update crew profiles.

Every modification shall generate an audit record.

## FR-002: Physiological Monitoring

The system shall monitor simulated physiological parameters, including:

- Heart rate (BPM).
- Systolic and diastolic blood pressure.
- Oxygen saturation (SpO2).
- Respiratory rate.
- Body temperature.
- Sleep duration.
- Hydration indicators.
- Weight and relevant body-composition measurements.

Requirements:

1. Measurements shall be associated with a crew member.
2. Every measurement shall include a timestamp.
3. Measurements shall be stored persistently.
4. The dashboard shall display the latest valid reading.
5. Historical readings shall be available for analysis.
6. Missing or invalid measurements shall be identified.
7. Thresholds shall be configurable.
8. Abnormal readings shall trigger the appropriate alert evaluation.

## FR-003: Radiation Monitoring

The system shall provide individual and mission-wide radiation exposure tracking.

It shall record:

- Instantaneous radiation measurements.
- Accumulated exposure.
- Daily exposure.
- Mission-total exposure.
- Radiation events.
- Exposure trends.

The system shall calculate cumulative exposure from validated measurements and display configurable warning and critical exposure states.

All radiation measurements shall retain their units, timestamps and source information.

## FR-004: Environmental Monitoring

The application shall monitor simulated spacecraft environmental conditions.

Parameters shall include:

- Cabin temperature.
- Cabin pressure.
- Oxygen concentration.
- Carbon dioxide concentration.
- Humidity.
- Air quality.
- Radiation conditions.

The application shall identify environmental measurements outside configured operating limits.

Environmental alerts shall be distinguishable from crew-health alerts.

## FR-005: Medical Records

Authorized operators shall be able to access and manage medical records.

Records shall include:

- Medical observations.
- Diagnoses entered by authorized personnel.
- Medical examinations.
- Medication history.
- Treatment notes.
- Medical incidents.
- Follow-up requirements.

Each record shall include its creator, creation timestamp and modification history.

The application shall not automatically convert an abnormal measurement into a confirmed diagnosis.

## FR-006: Medication Management

The system shall support medication inventory and administration tracking.

It shall maintain:

- Medication identifier.
- Medication name.
- Available quantity.
- Expiry date.
- Storage requirements.
- Administration history.
- Responsible operator.
- Administration timestamp.

The system shall provide configurable low-stock and expiry warnings.

Medication administration records shall be auditable and must not be silently overwritten.

## FR-007: Autonomous Alert Engine

The system shall implement a local rules-based alert engine.

The engine shall:

1. Evaluate incoming telemetry.
2. Compare measurements against configured thresholds.
3. Identify missing or stale measurements.
4. Generate alerts when rules are triggered.
5. Assign alert severity.
6. Record the triggering measurement.
7. Display relevant response guidance.
8. Allow authorized operators to acknowledge alerts.
9. Track alert resolution.
10. Maintain an alert history.

Alert severity shall include:

- INFORMATIONAL
- WARNING
- CRITICAL

Each alert shall contain:

- Unique alert identifier.
- Crew or environmental entity.
- Triggering condition.
- Measurement and units.
- Threshold or rule identifier.
- Severity.
- Detection timestamp.
- Acknowledgement status.
- Resolution status.

The system shall never silently discard a critical alert.

Decision-support recommendations shall be based on approved, configurable protocols and clearly identified as guidance requiring appropriate human review.

## FR-008: Earth Communication Simulation

The application shall simulate communication between the spacecraft and Earth.

The communication subsystem shall support:

- Outgoing messages.
- Incoming messages.
- Message priorities.
- Message timestamps.
- Delivery delays.
- Acknowledgements.
- Delivery failures.
- Message retries.
- Communication history.

The default mission configuration shall support:

- One-way delay: 18.7 minutes.
- Round-trip delay: 37.4 minutes.

The delay shall be configurable to simulate changing mission distances.

Messages shall be stored locally before transmission.

The system shall distinguish between:

- Queued.
- Transmitted.
- Delivered.
- Acknowledged.
- Failed.

A message shall never be represented as delivered before the simulated delivery event occurs.

## FR-009: Offline-First Operation

The application shall remain functional without an active connection to Earth.

It shall:

1. Store new telemetry locally.
2. Persist operator-entered information.
3. Continue local alert evaluation.
4. Maintain mission logs.
5. Queue outgoing communications.
6. Track synchronization status.
7. Resume synchronization after reconnection.
8. Prevent duplicate records during synchronization.
9. Handle synchronization conflicts explicitly.

Local data shall survive application restarts.

## FR-010: Mission Analytics

The application shall provide historical and comparative analytics.

Available visualizations shall include:

- Heart-rate history.
- Blood-pressure history.
- Oxygen saturation trends.
- Sleep patterns.
- Radiation exposure.
- Environmental conditions.
- Alert frequency.
- Crew-health comparisons.

Users shall be able to select a crew member and time interval.

Charts shall use actual stored measurements rather than hardcoded demonstration values.

## FR-011: Mission Event Logging

The application shall maintain a chronological mission event timeline.

Events shall include:

- Crew-health alerts.
- Medical observations.
- Medication administration.
- Communication events.
- Environmental warnings.
- Operator actions.
- System errors.
- Mission status changes.

Events shall include timestamps, event categories and associated entities.

## FR-012: Emergency Management

The system shall provide an emergency-management interface.

It shall support:

- Emergency event creation.
- Incident severity classification.
- Crew-member association.
- Approved response checklists.
- Assigned responsible operator.
- Event acknowledgement.
- Resolution tracking.
- Post-incident reporting.

Emergency procedures must remain accessible offline.

# 4. Non-Functional Requirements

## NFR-001: Performance

The application shall:

- Update the local dashboard within an agreed performance target of 1 second after processing a telemetry event under normal test conditions.
- Evaluate configured alert rules within 500 milliseconds of receiving a valid measurement under normal test conditions.
- Load standard dashboard views within 2 seconds under normal test conditions.
- Support continuous simulated telemetry without freezing the interface.

Performance targets shall be verified through repeatable tests.

## NFR-002: Reliability

The application shall:

- Persist critical records before confirming successful storage.
- Recover from unexpected application restarts.
- Identify incomplete synchronization.
- Avoid duplicate telemetry records.
- Handle unavailable communication services.
- Preserve previously recorded data after failures.

## NFR-003: Security

The application shall implement:

- Authentication.
- Role-based access control.
- Secure password handling.
- Input validation.
- Protection against unauthorized access.
- Secure communication where network transport is used.
- Encryption of sensitive persisted information where supported.
- Session management.
- Security event logging.

Sensitive information shall never be included in application logs unnecessarily.

## NFR-004: Auditability

The system shall implement tamper-evident audit records.

Audit entries shall include:

- Event identifier.
- User identifier.
- Action performed.
- Affected record.
- Previous and new values where appropriate.
- Timestamp.
- Event outcome.

Ordinary operators shall not be able to silently modify or erase historical audit records.

## NFR-005: Usability

The application shall:

- Provide a consistent navigation system.
- Clearly distinguish normal, warning and critical states.
- Display units beside physiological measurements.
- Provide understandable error messages.
- Avoid relying on colour alone to communicate alert severity.
- Support keyboard navigation.
- Provide readable text and sufficient contrast.
- Work across common desktop screen sizes.

## NFR-006: Maintainability

The codebase shall:

- Use modular components.
- Separate frontend and backend responsibilities.
- Avoid unnecessary code duplication.
- Include meaningful documentation.
- Use consistent naming conventions.
- Include automated tests.
- Maintain explicit dependency versions.
- Support future integrations.

## NFR-007: Scalability

The architecture shall support additional crew members, telemetry parameters, mission events and communication services without requiring a complete architectural redesign.

## NFR-008: Data Integrity

The application shall:

- Validate incoming data.
- Enforce unique record identifiers.
- Preserve measurement units.
- Record timestamps consistently.
- Detect invalid values.
- Prevent accidental record duplication.
- Track the source of measurements.

# 5. Data Management Requirements

## 5.1 Database Entities

The proposed database shall contain the following entities:

1. Users
2. Roles
3. CrewMembers
4. PhysiologicalMeasurements
5. RadiationMeasurements
6. EnvironmentalMeasurements
7. MedicalRecords
8. Medications
9. MedicationAdministrations
10. Alerts
11. EmergencyIncidents
12. CommunicationMessages
13. MissionEvents
14. AuditLogs
15. SynchronizationRecords

## 5.2 Data Retention

The application shall provide configurable retention policies.

Critical medical records and audit records shall not be automatically removed through ordinary user actions.

## 5.3 Data Export

Authorized users shall be able to export appropriate information in:

- CSV.
- JSON.
- PDF reports.

Export operations shall be logged.

# 6. User Roles and Permissions

The application shall support the following roles:

**Commander**
- Access mission overview.
- Review crew status.
- View mission reports.
- Acknowledge operational alerts.
- Manage mission-level settings.

**Medical Officer**
- Access crew medical information.
- Review physiological measurements.
- Manage authorized medical records.
- Record medication administration.
- Review and acknowledge medical alerts.

**Mission Operator**
- Monitor telemetry.
- Review communication messages.
- Access mission logs.
- Report operational incidents.

**Administrator**
- Manage user accounts.
- Configure system settings.
- Manage access permissions.
- Review security and audit records.

Access shall be enforced by the application, not merely by hiding interface elements.

# 7. User Interface Requirements

The application shall provide the following primary modules.

| Module | Responsibilities |
|---|---|
| Dashboard | Mission overview and crew status |
| Body | Individual crew physiological monitoring |
| Trends | Historical analytics and graphs |
| Earth Link | Communication and synchronization |
| Log | Audit and mission event history |
| Medical | Medical records and medications |
| Radiation | Exposure monitoring |
| Environment | Spacecraft environmental conditions |
| Alerts | Active and historical alerts |
| Emergency | Emergency procedures and incidents |
| Settings | Users, permissions and configurations |

The main dashboard shall display:

- Current mission day.
- Current mission location.
- Earth communication delay.
- Crew health overview.
- Active critical alerts.
- Radiation status.
- Environmental status.
- Last synchronization time.
- Data freshness indicators.

# 8. System Testing Requirements

## 8.1 Unit Testing

Test individual components, including:

- Telemetry validation.
- Threshold calculations.
- Alert generation.
- Radiation accumulation.
- Message scheduling.
- Permission checks.
- Data validation.

## 8.2 Integration Testing

Verify interactions between:

- Telemetry processing and database.
- Alert engine and dashboard.
- Communication queue and synchronization.
- Authentication and permissions.
- Medical records and audit logging.

## 8.3 Offline Testing

Verify that:

1. The application starts without Earth connectivity.
2. New telemetry is stored locally.
3. Local alerts continue functioning.
4. Messages remain queued.
5. Application restarts preserve data.
6. Reconnection initiates synchronization.
7. Synchronization does not create duplicate records.

## 8.4 Security Testing

Test:

- Unauthorized access.
- Invalid authentication.
- Permission escalation.
- Malformed inputs.
- Session expiry.
- Audit record protection.
- Sensitive data exposure.

## 8.5 Performance Testing

Simulate multiple crew members and continuous telemetry streams.

Measure:

- Processing latency.
- Database performance.
- Dashboard responsiveness.
- Memory consumption.
- Synchronization throughput.

# 9. Acceptance Criteria

The initial release shall be considered functionally complete when:

1. Crew members can be created and managed.
2. Simulated physiological measurements update the dashboard.
3. Measurements persist after restarting the application.
4. Historical trends display stored readings.
5. Configured alert rules generate the correct alerts.
6. Alerts can be acknowledged and resolved.
7. Radiation exposure is accumulated correctly.
8. Environmental measurements can be monitored.
9. Earth communication delays are simulated correctly.
10. Messages are queued during offline operation.
11. Synchronization resumes after reconnection.
12. Duplicate synchronization records are prevented.
13. Important operator actions generate audit records.
14. Role-based permissions are enforced.
15. Automated tests pass.

# 10. Future Development

Future versions may introduce:

- Hardware sensor integration.
- Wearable biomedical devices.
- Advanced anomaly detection.
- Predictive health modelling.
- Machine-learning-assisted telemetry analysis.
- More sophisticated mission simulations.
- External mission-control integrations.
- Distributed data synchronization.
- Advanced crew psychological wellbeing monitoring.
- Spacecraft resource and life-support monitoring.

Machine-learning outputs shall remain explainable and shall not independently authorize medical treatment or override approved safety protocols.

# 11. Technical Implementation Principles

The following principles shall guide implementation:

1. Offline-first rather than cloud-dependent operation.
2. Local autonomy rather than dependence on Earth responses.
3. Data integrity rather than convenience-based record modification.
4. Explainable alerts rather than opaque predictions.
5. Human-supervised medical decision support.
6. Modular architecture rather than tightly coupled components.
7. Automated testing rather than manual verification alone.
8. Secure-by-design development.
9. Reproducible builds and documented deployments.
10. Explicit handling of failures and uncertain data.

**End of Software Requirements Specification – CHECKPOINT v1.0**