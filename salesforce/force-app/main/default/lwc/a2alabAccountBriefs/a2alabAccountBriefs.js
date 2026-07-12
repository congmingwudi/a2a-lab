import { LightningElement, api, wire } from 'lwc';
import { getRelatedListRecords } from 'lightning/uiRelatedListApi';
import { mdToHtml } from 'c/a2alabMarkdown';

const FIELDS = [
    'A2ALab_Account_Brief__c.Id',
    'A2ALab_Account_Brief__c.Name',
    'A2ALab_Account_Brief__c.Brief__c',
    'A2ALab_Account_Brief__c.Brief_Date__c',
    'A2ALab_Account_Brief__c.Source__c',
    'A2ALab_Account_Brief__c.Research_Session_Id__c',
    'A2ALab_Account_Brief__c.CreatedDate'
];

export default class A2alabAccountBriefs extends LightningElement {
    @api recordId;
    briefs = [];
    error;
    loaded = false;

    @wire(getRelatedListRecords, {
        parentRecordId: '$recordId',
        relatedListId: 'A2ALab_Account_Briefs__r',
        fields: FIELDS,
        pageSize: 50
    })
    wired({ data, error }) {
        if (data) {
            const val = (r, f) => (r.fields[f] ? r.fields[f].value : null);
            this.briefs = (data.records || [])
                .map((r) => ({
                    id: val(r, 'Id'),
                    name: val(r, 'Name'),
                    date: val(r, 'Brief_Date__c'),
                    source: val(r, 'Source__c'),
                    session: val(r, 'Research_Session_Id__c'),
                    created: val(r, 'CreatedDate'),
                    brief: val(r, 'Brief__c'),
                    url: '/' + val(r, 'Id')
                }))
                .sort((a, b) => (a.created < b.created ? 1 : -1));
            this.error = undefined;
            this.loaded = true;
        } else if (error) {
            const body = error.body || error;
            this.error = (body.message || JSON.stringify(body)).slice(0, 300);
            this.loaded = true;
        }
    }

    get latest() {
        return this.briefs.length ? this.briefs[0] : null;
    }

    get latestHtml() {
        return this.latest ? mdToHtml(this.latest.brief || '(empty brief)') : '';
    }

    get hasBriefs() {
        return this.briefs.length > 0;
    }

    get showEmpty() {
        return this.loaded && !this.error && this.briefs.length === 0;
    }

    get pastBriefs() {
        return this.briefs.slice(1);
    }

    get hasPast() {
        return this.briefs.length > 1;
    }
}
