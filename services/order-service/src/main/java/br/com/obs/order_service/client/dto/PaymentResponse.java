package br.com.obs.order_service.client.dto;

public class PaymentResponse {

    private Long paymentId;
    private String status;

    public PaymentResponse() {
    }

    public PaymentResponse(Long paymentId, String status) {
        this.paymentId = paymentId;
        this.status = status;
    }

    public Long getPaymentId() {
        return paymentId;
    }

    public void setPaymentId(Long paymentId) {
        this.paymentId = paymentId;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }
}